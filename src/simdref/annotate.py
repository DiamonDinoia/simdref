"""Annotate assembly (``.s``) files with per-instruction summaries and perf.

Given GAS/AT&T-syntax assembly, emit an annotated ``.sa`` file where each
recognised instruction line carries a trailing ``# ...`` comment describing
what the instruction does plus latency / CPI figures pulled from the
simdref catalog.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator

from simdref import perf
from simdref.models import InstructionRecord
from simdref.storage import load_instructions_by_mnemonic_from_db


class LineKind(str, Enum):
    BLANK = "blank"
    LABEL = "label"
    DIRECTIVE = "directive"
    COMMENT = "comment"
    INSTRUCTION = "instruction"


@dataclass(slots=True)
class AsmLine:
    kind: LineKind
    raw: str
    indent: str = ""
    mnemonic: str = ""
    operands: str = ""
    trailing_comment: str = ""
    address: int | None = None
    source_file: str | None = None
    source_line: int | None = None


_INSTR_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<mnemonic>[A-Za-z][A-Za-z0-9_.]*)"
    r"(?:[ \t]+(?P<operands>[^#\n]*?))?"
    r"(?:[ \t]*(?P<comment>#.*))?$"
)
_LABEL_RE = re.compile(r"^[ \t]*[A-Za-z_.$][\w.$]*:")
# objdump -d line shape: optional whitespace, hex VA, ':', hex bytes, mnemonic ops.
_OBJDUMP_INSTR_RE = re.compile(
    r"^\s*(?P<addr>[0-9a-fA-F]+):\s+"
    r"(?:(?:[0-9a-fA-F]{2}\s+){1,10})?"
    r"(?P<rest>\S.*?)\s*$"
)
# objdump -S injects "file:line" comment lines before the instruction block.
_OBJDUMP_SRC_RE = re.compile(r"^\s*(?P<file>[^ \t/][^:]*):(?P<line>\d+)\s*$")


def parse_asm_line(line: str, *, track_positions: bool = False) -> AsmLine:
    stripped = line.rstrip("\n")
    if not stripped.strip():
        return AsmLine(LineKind.BLANK, stripped)
    bare = stripped.lstrip()
    if bare.startswith("#") or bare.startswith("//"):
        return AsmLine(LineKind.COMMENT, stripped)
    if bare.startswith("."):
        return AsmLine(LineKind.DIRECTIVE, stripped)
    if _LABEL_RE.match(stripped):
        return AsmLine(LineKind.LABEL, stripped)

    address: int | None = None
    if track_positions:
        obj_m = _OBJDUMP_INSTR_RE.match(stripped)
        if obj_m:
            try:
                address = int(obj_m.group("addr"), 16)
            except ValueError:
                address = None
            rest = obj_m.group("rest")
            m = _INSTR_RE.match(rest)
            if m:
                return AsmLine(
                    kind=LineKind.INSTRUCTION,
                    raw=stripped,
                    indent=(stripped[: stripped.find(rest)] if rest in stripped else ""),
                    mnemonic=m.group("mnemonic") or "",
                    operands=(m.group("operands") or "").strip(),
                    trailing_comment=(m.group("comment") or "").strip(),
                    address=address,
                )

    m = _INSTR_RE.match(stripped)
    if not m:
        return AsmLine(LineKind.COMMENT, stripped)
    return AsmLine(
        kind=LineKind.INSTRUCTION,
        raw=stripped,
        indent=m.group("indent") or "",
        mnemonic=m.group("mnemonic") or "",
        operands=(m.group("operands") or "").strip(),
        trailing_comment=(m.group("comment") or "").strip(),
        address=address,
    )


# ---------------------------------------------------------------------------
# Catalog lookup
# ---------------------------------------------------------------------------


# Common AT&T size suffixes that may be absent from the catalog's Intel form.
_ATT_SUFFIXES = ("b", "w", "l", "q", "s", "d", "t")


def _lookup_variants(mnemonic: str) -> list[str]:
    """Candidate mnemonics to try against the catalog, in preference order."""
    base = mnemonic.lower()
    out = [base]
    # Strip a single trailing size suffix (AT&T style) if present.
    if len(base) > 2 and base[-1] in _ATT_SUFFIXES:
        trimmed = base[:-1]
        if trimmed not in out:
            out.append(trimmed)
    return out


def lookup(mnemonic: str, conn: sqlite3.Connection) -> list[InstructionRecord]:
    for cand in _lookup_variants(mnemonic):
        records = load_instructions_by_mnemonic_from_db(conn, cand)
        if records:
            return records
    return []


_SIZE_SPECIFIER_RE = re.compile(
    r"\b(byte|word|dword|qword|xmmword|ymmword|zmmword)\s+ptr\b",
    re.IGNORECASE,
)
_SIZE_TO_BITS = {
    "byte": "8", "word": "16", "dword": "32", "qword": "64",
    "xmmword": "128", "ymmword": "256", "zmmword": "512",
}
# Register classes ordered so longer/wider names match first.
_REG_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:r[abcd]x|r[sd]i|rbp|rsp|r(?:8|9|1[0-5]))\b"), "R64"),
    (re.compile(r"\b(?:e[abcd]x|e[sd]i|ebp|esp|r(?:8|9|1[0-5])d)\b"), "R32"),
    (re.compile(r"\b(?:[abcd]x|[sd]i|bp|sp|r(?:8|9|1[0-5])w)\b"), "R16"),
    (re.compile(r"\b(?:[abcd][lh]|[sd]il|bpl|spl|r(?:8|9|1[0-5])b)\b"), "R8"),
    (re.compile(r"\bxmm\d+\b"), "XMM"),
    (re.compile(r"\bymm\d+\b"), "YMM"),
    (re.compile(r"\bzmm\d+\b"), "ZMM"),
    (re.compile(r"\bk[0-7]\b"), "K"),
)


def _operand_width_tokens(operands: str) -> list[str]:
    if not operands:
        return []
    s = operands.lower()
    tokens: list[str] = []
    for m in _SIZE_SPECIFIER_RE.finditer(s):
        bits = _SIZE_TO_BITS[m.group(1).lower()]
        tokens.append(f"M{bits}")
    for regex, tok in _REG_PATTERNS:
        if regex.search(s):
            tokens.append(tok)
    return tokens


def _operand_match_score(record: InstructionRecord, tokens: list[str]) -> int:
    if not tokens:
        return 0
    key = str(getattr(record, "key", "") or "").upper()
    score = 0
    for tok in tokens:
        if re.search(rf"\b{tok}\b", key):
            score += 10
        elif tok.startswith("R") and ("M" + tok[1:]) in key:
            score += 2
    return score


def pick_record(
    records: list[InstructionRecord],
    *,
    arch: str | None = None,
    operands: str = "",
) -> InstructionRecord | None:
    if not records:
        return None
    candidates = records
    if arch is not None:
        pinned = [r for r in records if arch in (r.arch_details or {})]
        if pinned:
            candidates = pinned

    op_tokens = _operand_width_tokens(operands)

    def score(rec: InstructionRecord) -> tuple[int, int, int]:
        measured = sum(
            1 for d in (rec.arch_details or {}).values()
            if (d.get("source_kind") or "measured") == "measured"
        )
        op_score = _operand_match_score(rec, op_tokens)
        # Operand match dominates; measurement coverage tiebreaks.
        return (op_score, measured, len(rec.arch_details or {}))

    return max(candidates, key=score)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PerfSummary:
    latency: float | None
    cpi: float | None
    n_archs: int
    source_kind: str  # "measured", "modeled", "mixed"
    archs_used: list[str] = field(default_factory=list)


def _per_arch_value(details: dict[str, Any], value_fn) -> float | None:
    for v in value_fn(details):
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _latency_for(details: dict[str, Any]) -> list[str]:
    return perf.latency_cycle_values(details.get("latencies") or [])


def _cpi_for(details: dict[str, Any]) -> list[str]:
    return perf._cpi_values(details)


def aggregate_perf(
    record: InstructionRecord,
    *,
    mode: str = "avg",
    include_modeled: bool = False,
) -> PerfSummary:
    """Aggregate latency and CPI across ``record``'s measured microarches."""
    arch_details = record.arch_details or {}

    def collect(kinds: tuple[str, ...]) -> tuple[list[float], list[float], list[str]]:
        lats: list[float] = []
        cpis: list[float] = []
        archs: list[str] = []
        for core, details in arch_details.items():
            kind = details.get("source_kind") or "measured"
            if kind not in kinds:
                continue
            lat = _per_arch_value(details, _latency_for)
            cpi = _per_arch_value(details, _cpi_for)
            if lat is None and cpi is None:
                continue
            archs.append(core)
            if lat is not None:
                lats.append(lat)
            if cpi is not None:
                cpis.append(cpi)
        return lats, cpis, archs

    lats, cpis, archs = collect(("measured",))
    source_kind = "measured"
    if not archs and include_modeled:
        lats, cpis, archs = collect(("modeled",))
        source_kind = "modeled"
    if not archs:
        # Last-ditch: anything at all.
        lats, cpis, archs = collect(("measured", "modeled"))
        source_kind = "mixed"

    def reduce(values: list[float]) -> float | None:
        if not values:
            return None
        if mode == "avg":
            return statistics.fmean(values)
        if mode == "median":
            return statistics.median(values)
        if mode == "best":
            return min(values)
        if mode == "worst":
            return max(values)
        raise ValueError(f"unknown --agg mode: {mode}")

    return PerfSummary(
        latency=reduce(lats),
        cpi=reduce(cpis),
        n_archs=len(archs),
        source_kind=source_kind,
        archs_used=archs,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_num(x: float | None) -> str:
    if x is None:
        return "-"
    if float(x).is_integer():
        return f"{x:.1f}"
    return f"{x:.2f}"


def _arch_perf(
    record: InstructionRecord, arch: str
) -> tuple[float | None, float | None, str]:
    details = (record.arch_details or {}).get(arch) or {}
    kind = details.get("source_kind") or "measured"
    lat = _per_arch_value(details, _latency_for)
    cpi = _per_arch_value(details, _cpi_for)
    return lat, cpi, kind


_SUMMARY_BITS_RE = re.compile(r"(\d+)\s*-?\s*bit", re.IGNORECASE)
_FORM_WIDTH_RE = re.compile(r"[MRI](\d+)")


def _summary_matches_form(summary: str, record: InstructionRecord) -> bool:
    """Drop obviously mislabeled SDM summaries (e.g. the generic MOV
    blurb "Move 32-bit integer operands." attached to MOV (M64, R64)).

    We only flag a summary as wrong when it declares an explicit bit-width
    that appears nowhere among the form's operand widths."""
    m = _SUMMARY_BITS_RE.search(summary or "")
    if not m:
        return True
    declared = m.group(1)
    widths: list[str] = []
    for op in record.operand_details or []:
        w = op.get("width")
        if w:
            widths.append(str(w))
    if not widths:
        key = str(getattr(record, "key", "") or "")
        widths = _FORM_WIDTH_RE.findall(key)
    if not widths:
        return True
    return declared in widths


def format_annotation(
    record: InstructionRecord,
    *,
    performance: bool,
    docs: bool,
    arch: str | None,
    agg: str,
    include_modeled: bool,
) -> str:
    """Compose the comment fragment (without the leading ``# `` marker)."""
    parts: list[str] = []
    if docs and record.summary and _summary_matches_form(record.summary, record):
        parts.append(record.summary.strip())
    if performance:
        if arch is not None:
            lat, cpi, kind = _arch_perf(record, arch)
            tag = f"[{arch}, {kind}]"
        else:
            summary = aggregate_perf(
                record, mode=agg, include_modeled=include_modeled
            )
            lat, cpi = summary.latency, summary.cpi
            if summary.n_archs == 0:
                tag = "[no data]"
            elif arch is None and agg == "avg":
                tag = f"[avg of {summary.n_archs} archs, {summary.source_kind}]"
            else:
                tag = f"[{agg} of {summary.n_archs} archs, {summary.source_kind}]"
        perf_frag = f"lat={_fmt_num(lat)}c cpi={_fmt_num(cpi)} {tag}"
        parts.append(perf_frag)
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Options & streaming
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AnnotateOptions:
    performance: bool = True
    docs: bool = True
    arch: str | None = None
    agg: str = "avg"
    include_modeled: bool = False
    block: bool = False  # otherwise inline
    unknown: str = "mark"  # "keep" | "drop" | "mark"
    fmt: str = "sa"  # "sa" | "md" | "json"
    track_positions: bool = False


def _annotate_instruction(
    parsed: AsmLine,
    opts: AnnotateOptions,
    conn: sqlite3.Connection,
) -> tuple[str, dict[str, Any] | None]:
    """Return the rendered output line and an optional JSON record."""
    records = lookup(parsed.mnemonic, conn)
    record = pick_record(records, arch=opts.arch, operands=parsed.operands)

    if record is None:
        if opts.unknown == "drop":
            return parsed.raw, None
        if opts.unknown == "mark":
            marker = "# ??"
            if parsed.trailing_comment:
                return parsed.raw, None
            unknown_rec: dict[str, Any] = {
                "mnemonic": parsed.mnemonic,
                "known": False,
            }
            if parsed.address is not None:
                unknown_rec["address"] = f"0x{parsed.address:x}"
            if parsed.source_file:
                unknown_rec["source_file"] = parsed.source_file
            if parsed.source_line is not None:
                unknown_rec["source_line"] = parsed.source_line
            return f"{parsed.raw}   {marker}", unknown_rec
        return parsed.raw, None

    if not (opts.performance or opts.docs):
        return parsed.raw, None

    annotation = format_annotation(
        record,
        performance=opts.performance,
        docs=opts.docs,
        arch=opts.arch,
        agg=opts.agg,
        include_modeled=opts.include_modeled,
    )
    if not annotation:
        return parsed.raw, None

    json_record: dict[str, Any] = {
        "mnemonic": parsed.mnemonic,
        "known": True,
        "summary": record.summary,
        "annotation": annotation,
    }
    if parsed.address is not None:
        json_record["address"] = f"0x{parsed.address:x}"
    if parsed.source_file:
        json_record["source_file"] = parsed.source_file
    if parsed.source_line is not None:
        json_record["source_line"] = parsed.source_line

    if opts.block:
        block_line = f"{parsed.indent}# {annotation}"
        return f"{block_line}\n{parsed.raw}", json_record

    # Inline: append after the raw line, respecting any pre-existing comment.
    if parsed.trailing_comment:
        return parsed.raw, json_record
    return f"{parsed.raw}   # {annotation}", json_record


def annotate_stream(
    lines: Iterable[str],
    *,
    opts: AnnotateOptions,
    conn: sqlite3.Connection,
) -> Iterator[str]:
    """Yield annotated lines for each input line (newline-terminated)."""
    json_records: list[dict[str, Any]] = []
    collecting_json = opts.fmt == "json"
    pending_src_file: str | None = None
    pending_src_line: int | None = None

    for line in lines:
        if opts.track_positions:
            src_m = _OBJDUMP_SRC_RE.match(line.rstrip("\n"))
            if src_m:
                pending_src_file = src_m.group("file").strip()
                try:
                    pending_src_line = int(src_m.group("line"))
                except ValueError:
                    pending_src_line = None
                if not collecting_json:
                    yield line if line.endswith("\n") else line + "\n"
                continue
        parsed = parse_asm_line(line, track_positions=opts.track_positions)
        if opts.track_positions and parsed.kind == LineKind.INSTRUCTION:
            parsed.source_file = pending_src_file
            parsed.source_line = pending_src_line
        if parsed.kind != LineKind.INSTRUCTION:
            if not collecting_json:
                yield parsed.raw + "\n"
            continue
        out_line, record = _annotate_instruction(parsed, opts, conn)
        if collecting_json:
            if record is not None:
                json_records.append(record)
            continue
        yield out_line + "\n"

    if collecting_json:
        yield json.dumps(json_records, indent=2) + "\n"
