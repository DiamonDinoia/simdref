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


_INSTR_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<mnemonic>[A-Za-z][A-Za-z0-9_.]*)"
    r"(?:[ \t]+(?P<operands>[^#\n]*?))?"
    r"(?:[ \t]*(?P<comment>#.*))?$"
)
_LABEL_RE = re.compile(r"^[ \t]*[A-Za-z_.$][\w.$]*:")


def parse_asm_line(line: str) -> AsmLine:
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


def pick_record(
    records: list[InstructionRecord],
    *,
    arch: str | None = None,
) -> InstructionRecord | None:
    if not records:
        return None
    if arch is not None:
        for rec in records:
            if arch in (rec.arch_details or {}):
                return rec
    # Prefer the record with most measured-arch coverage, then most archs.
    def score(rec: InstructionRecord) -> tuple[int, int]:
        measured = sum(
            1 for d in (rec.arch_details or {}).values()
            if (d.get("source_kind") or "measured") == "measured"
        )
        return (measured, len(rec.arch_details or {}))

    return max(records, key=score)


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
    if docs and record.summary:
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


def _annotate_instruction(
    parsed: AsmLine,
    opts: AnnotateOptions,
    conn: sqlite3.Connection,
) -> tuple[str, dict[str, Any] | None]:
    """Return the rendered output line and an optional JSON record."""
    records = lookup(parsed.mnemonic, conn)
    record = pick_record(records, arch=opts.arch)

    if record is None:
        if opts.unknown == "drop":
            return parsed.raw, None
        if opts.unknown == "mark":
            marker = "# ??"
            if parsed.trailing_comment:
                return parsed.raw, None
            return f"{parsed.raw}   {marker}", {
                "mnemonic": parsed.mnemonic,
                "known": False,
            }
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

    json_record = {
        "mnemonic": parsed.mnemonic,
        "known": True,
        "summary": record.summary,
        "annotation": annotation,
    }

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

    for line in lines:
        parsed = parse_asm_line(line)
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
