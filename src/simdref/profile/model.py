"""Data types for runtime profile samples and detected loops."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class SampleRow:
    """One observation from a profiler, normalized across tools.

    Multiple events (cycles, cache-misses, ...) live side-by-side in the
    same file, keyed by ``event``.
    """

    address: int
    event: str
    samples: int
    weight: float
    section: str = ".text"
    symbol: str = ""
    source_file: str | None = None
    source_line: int | None = None
    basic_block_id: int | None = None
    source_kind: str = "measured"  # "measured" | "modeled"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["address"] = f"0x{self.address:x}"
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SampleRow":
        addr = d["address"]
        if isinstance(addr, str):
            addr = int(addr, 16) if addr.startswith(("0x", "0X")) else int(addr)
        return cls(
            address=int(addr),
            event=str(d["event"]),
            samples=int(d.get("samples", 0)),
            weight=float(d.get("weight", 0.0)),
            section=str(d.get("section", ".text")),
            symbol=str(d.get("symbol", "")),
            source_file=d.get("source_file"),
            source_line=(int(d["source_line"]) if d.get("source_line") is not None else None),
            basic_block_id=(int(d["basic_block_id"]) if d.get("basic_block_id") is not None else None),
            source_kind=str(d.get("source_kind", "measured")),
        )


@dataclass(frozen=True, slots=True)
class LoopRegion:
    """A natural loop identified by a back-edge in the CFG."""

    loop_id: int
    symbol: str
    entry_address: int
    exit_address: int
    addresses: tuple[int, ...]
    back_edges: tuple[tuple[int, int], ...]
    nesting_depth: int = 0
    total_weight: float = 0.0
    source_file: str | None = None
    source_line_start: int | None = None
    source_line_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop_id": self.loop_id,
            "symbol": self.symbol,
            "entry_address": f"0x{self.entry_address:x}",
            "exit_address": f"0x{self.exit_address:x}",
            "addresses": [f"0x{a:x}" for a in self.addresses],
            "back_edges": [[f"0x{s:x}", f"0x{t:x}"] for s, t in self.back_edges],
            "nesting_depth": self.nesting_depth,
            "total_weight": self.total_weight,
            "source_file": self.source_file,
            "source_line_start": self.source_line_start,
            "source_line_end": self.source_line_end,
        }


def write_samples(samples: Iterable[SampleRow], path: Path) -> None:
    """Write samples to JSON."""
    out = {"schema": "simdref.samples.v1", "samples": [s.to_dict() for s in samples]}
    path.write_text(json.dumps(out, indent=2) + "\n")


def read_samples(path: Path) -> list[SampleRow]:
    data = json.loads(path.read_text())
    return [SampleRow.from_dict(d) for d in data.get("samples", [])]


def write_loops(loops: Iterable[LoopRegion], path: Path) -> None:
    out = {"schema": "simdref.loops.v1", "loops": [l.to_dict() for l in loops]}
    path.write_text(json.dumps(out, indent=2) + "\n")


def read_loops(path: Path) -> list[LoopRegion]:
    data = json.loads(path.read_text())
    loops: list[LoopRegion] = []
    for d in data.get("loops", []):
        def _h(x: Any) -> int:
            if isinstance(x, str):
                return int(x, 16) if x.startswith(("0x", "0X")) else int(x)
            return int(x)

        loops.append(
            LoopRegion(
                loop_id=int(d["loop_id"]),
                symbol=str(d["symbol"]),
                entry_address=_h(d["entry_address"]),
                exit_address=_h(d["exit_address"]),
                addresses=tuple(_h(a) for a in d.get("addresses", [])),
                back_edges=tuple((_h(s), _h(t)) for s, t in d.get("back_edges", [])),
                nesting_depth=int(d.get("nesting_depth", 0)),
                total_weight=float(d.get("total_weight", 0.0)),
                source_file=d.get("source_file"),
                source_line_start=d.get("source_line_start"),
                source_line_end=d.get("source_line_end"),
            )
        )
    return loops
