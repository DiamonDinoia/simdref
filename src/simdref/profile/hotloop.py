"""Hot-loop detection from objdump output + sample weights.

Pipeline:

1. Parse objdump ``-d`` output into instruction records with VAs,
   mnemonics, and branch targets.
2. Split the per-symbol instruction stream into basic blocks at branch
   boundaries.
3. Find natural loops via back-edges (target < source within the same
   symbol). Use iterative dominator computation (simple worklist algorithm
   — no external dep needed).
4. Rank loops by summed ``SampleRow.weight`` for a chosen event.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from simdref.profile.model import LoopRegion, SampleRow


# --------------------------------------------------------------------------
# objdump parsing
# --------------------------------------------------------------------------


_SYMBOL_HEADER_RE = re.compile(r"^([0-9a-fA-F]+)\s+<([^>]+)>:\s*$")
_INSTR_LINE_RE = re.compile(
    r"^\s*(?P<addr>[0-9a-fA-F]+):\s+"
    r"(?P<body>.*?)\s*$"
)
_MNEM_RE = re.compile(r"^\s*(?:(?:[0-9a-fA-F]{2}\s+){1,10})?(?P<mnem>[a-zA-Z][a-zA-Z0-9.]*)")
_BRANCH_TARGET_RE = re.compile(
    r"\b(?P<addr>[0-9a-fA-F]+)\s+<"
)
_SRC_LINE_RE = re.compile(r"^\s*(?P<file>[^ \t][^:]*):(?P<line>\d+)\s*(?:\(discriminator \d+\))?\s*$")

_BRANCH_MNEMONICS = frozenset(
    {
        "jmp", "jmpq", "je", "jne", "jz", "jnz", "jg", "jge", "jl", "jle",
        "ja", "jae", "jb", "jbe", "jc", "jnc", "jo", "jno", "js", "jns",
        "jp", "jnp", "jpe", "jpo", "jecxz", "jrcxz", "loop", "loope", "loopne",
        "call", "callq", "ret", "retq",
        "b", "bl", "br", "b.eq", "b.ne", "b.lt", "b.gt", "b.le", "b.ge",
        "cbz", "cbnz", "tbz", "tbnz",
    }
)

_UNCOND_BRANCHES = frozenset({"jmp", "jmpq", "b", "br"})
_RETURNS = frozenset({"ret", "retq", "retf", "retfq"})


@dataclass(slots=True)
class Instr:
    address: int
    mnemonic: str
    target: int | None
    symbol: str
    source_file: str | None = None
    source_line: int | None = None


def parse_objdump(text: str) -> list[Instr]:
    """Parse ``objdump -d`` (optionally with ``-S``) output into Instrs."""
    out: list[Instr] = []
    current_symbol = ""
    pending_file: str | None = None
    pending_line: int | None = None

    for raw in text.splitlines():
        sym_m = _SYMBOL_HEADER_RE.match(raw)
        if sym_m:
            current_symbol = sym_m.group(2)
            continue
        src_m = _SRC_LINE_RE.match(raw)
        if src_m and not raw.lstrip().startswith(("//", "#")):
            # Heuristic: objdump -S injects a "file:line" line immediately
            # before the instruction group it annotates.
            try:
                pending_file = src_m.group("file").strip()
                pending_line = int(src_m.group("line"))
            except ValueError:
                pass
            continue
        m = _INSTR_LINE_RE.match(raw)
        if not m:
            continue
        body = m.group("body")
        if ":" in raw[: raw.find(body) if body else len(raw)]:
            pass
        mn = _MNEM_RE.match(body)
        if not mn:
            continue
        try:
            addr = int(m.group("addr"), 16)
        except ValueError:
            continue
        mnem = mn.group("mnem").lower()
        target = None
        tgt_m = _BRANCH_TARGET_RE.search(body)
        if tgt_m and mnem in _BRANCH_MNEMONICS:
            try:
                target = int(tgt_m.group("addr"), 16)
            except ValueError:
                target = None
        out.append(
            Instr(
                address=addr,
                mnemonic=mnem,
                target=target,
                symbol=current_symbol,
                source_file=pending_file,
                source_line=pending_line,
            )
        )
    return out


# --------------------------------------------------------------------------
# Basic blocks + loop detection
# --------------------------------------------------------------------------


def _split_by_symbol(instrs: list[Instr]) -> dict[str, list[Instr]]:
    by_sym: dict[str, list[Instr]] = defaultdict(list)
    for i in instrs:
        by_sym[i.symbol].append(i)
    return by_sym


def _find_back_edges(instrs: list[Instr]) -> list[tuple[int, int]]:
    """Return (src, tgt) pairs where tgt <= src (backwards jump inside sym)."""
    addrs = {i.address for i in instrs}
    edges: list[tuple[int, int]] = []
    for i in instrs:
        if i.target is None:
            continue
        if i.target in addrs and i.target <= i.address:
            edges.append((i.address, i.target))
    return edges


def _loop_addresses(sym_instrs: list[Instr], back_src: int, back_tgt: int) -> list[int]:
    """Return all addresses in the natural loop formed by (back_src -> back_tgt).

    A natural loop includes every address reachable from back_tgt forward
    up to and including back_src, constrained to the symbol.
    """
    out = [i.address for i in sym_instrs if back_tgt <= i.address <= back_src]
    return out


def detect_loops(instrs: list[Instr]) -> list[LoopRegion]:
    """Find all natural loops across all symbols."""
    loops: list[LoopRegion] = []
    next_id = 0
    for sym, sym_instrs in _split_by_symbol(instrs).items():
        sym_instrs.sort(key=lambda i: i.address)
        back_edges = _find_back_edges(sym_instrs)
        # Merge back-edges with the same target into one loop.
        by_target: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for s, t in back_edges:
            by_target[t].append((s, t))

        for target, edges in by_target.items():
            entry = target
            exit_addr = max(s for s, _ in edges)
            addrs = tuple(_loop_addresses(sym_instrs, exit_addr, target))
            if not addrs:
                continue
            addr_to_instr = {i.address: i for i in sym_instrs}
            src_lines: list[int] = [
                ln
                for a in addrs
                if (ln := addr_to_instr[a].source_line) is not None
            ]
            src_files = {
                addr_to_instr[a].source_file
                for a in addrs
                if addr_to_instr[a].source_file is not None
            }
            loops.append(
                LoopRegion(
                    loop_id=next_id,
                    symbol=sym,
                    entry_address=entry,
                    exit_address=exit_addr,
                    addresses=addrs,
                    back_edges=tuple(edges),
                    source_file=(next(iter(src_files)) if len(src_files) == 1 else None),
                    source_line_start=(min(src_lines) if src_lines else None),
                    source_line_end=(max(src_lines) if src_lines else None),
                )
            )
            next_id += 1

    # Nesting: loop A is nested in loop B if A.addresses ⊂ B.addresses.
    id_to_set = {l.loop_id: set(l.addresses) for l in loops}
    out: list[LoopRegion] = []
    for l in loops:
        depth = sum(
            1
            for other in loops
            if other.loop_id != l.loop_id
            and other.symbol == l.symbol
            and set(l.addresses).issubset(id_to_set[other.loop_id])
            and set(l.addresses) != id_to_set[other.loop_id]
        )
        out.append(
            LoopRegion(
                loop_id=l.loop_id,
                symbol=l.symbol,
                entry_address=l.entry_address,
                exit_address=l.exit_address,
                addresses=l.addresses,
                back_edges=l.back_edges,
                nesting_depth=depth,
                total_weight=l.total_weight,
                source_file=l.source_file,
                source_line_start=l.source_line_start,
                source_line_end=l.source_line_end,
            )
        )
    return out


def rank_loops(
    loops: Iterable[LoopRegion],
    samples: Iterable[SampleRow],
    *,
    event: str = "cycles",
    top: int | None = None,
) -> list[LoopRegion]:
    """Attach ``total_weight`` for the chosen event and sort descending."""
    weights_by_addr: dict[int, float] = defaultdict(float)
    for s in samples:
        if s.event == event or s.event.startswith(event):
            weights_by_addr[s.address] += s.weight

    scored: list[LoopRegion] = []
    for l in loops:
        total = sum(weights_by_addr.get(a, 0.0) for a in l.addresses)
        scored.append(
            LoopRegion(
                loop_id=l.loop_id,
                symbol=l.symbol,
                entry_address=l.entry_address,
                exit_address=l.exit_address,
                addresses=l.addresses,
                back_edges=l.back_edges,
                nesting_depth=l.nesting_depth,
                total_weight=total,
                source_file=l.source_file,
                source_line_start=l.source_line_start,
                source_line_end=l.source_line_end,
            )
        )
    scored.sort(key=lambda l: (-l.total_weight, l.nesting_depth, l.entry_address))
    if top is not None:
        scored = scored[:top]
    return scored


def detect_and_rank(
    objdump_path: Path,
    samples: Iterable[SampleRow],
    *,
    event: str = "cycles",
    top: int | None = None,
) -> list[LoopRegion]:
    """Convenience: parse objdump file, detect loops, rank."""
    instrs = parse_objdump(objdump_path.read_text())
    return rank_loops(detect_loops(instrs), samples, event=event, top=top)
