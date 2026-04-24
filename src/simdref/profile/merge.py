"""Merge annotated-JSON with runtime samples.

The annotate JSON (see ``simdref annotate --format json --track-positions``)
carries per-instruction ``address`` and/or ``source_file``/``source_line``.
This module joins that stream with a list of ``SampleRow`` on those keys
and writes back an augmented stream with a ``hotness`` block.

Distinct from ``simdref.perf_sources.merge`` — that module ingests static
catalogs; this module attaches runtime observations to annotated
instructions without touching the catalog.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from simdref.profile.model import LoopRegion, SampleRow


@dataclass(slots=True)
class MergedRecord:
    mnemonic: str
    address: int | None
    source_file: str | None
    source_line: int | None
    annotation: str | None
    hotness: dict[str, Any]
    summary: str | None = None
    known: bool = True
    raw: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"mnemonic": self.mnemonic, "known": self.known}
        if self.address is not None:
            d["address"] = f"0x{self.address:x}"
        if self.source_file:
            d["source_file"] = self.source_file
        if self.source_line is not None:
            d["source_line"] = self.source_line
        if self.summary:
            d["summary"] = self.summary
        if self.annotation:
            d["annotation"] = self.annotation
        if self.hotness:
            d["hotness"] = self.hotness
        if self.raw:
            d["raw"] = self.raw
        return d


def _group_samples(
    samples: Iterable[SampleRow],
) -> tuple[
    dict[int, dict[str, list[SampleRow]]],
    dict[tuple[str, int], dict[str, list[SampleRow]]],
]:
    by_addr: dict[int, dict[str, list[SampleRow]]] = defaultdict(lambda: defaultdict(list))
    by_srcline: dict[tuple[str, int], dict[str, list[SampleRow]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for s in samples:
        by_addr[s.address][s.event].append(s)
        if s.source_file and s.source_line is not None:
            by_srcline[(s.source_file, s.source_line)][s.event].append(s)
    return by_addr, by_srcline


def _summarize_event_bucket(rows: list[SampleRow]) -> dict[str, Any]:
    samples = sum(r.samples for r in rows)
    weight = sum(r.weight for r in rows)
    kinds = {r.source_kind for r in rows}
    kind = "measured" if kinds == {"measured"} else ("modeled" if kinds == {"modeled"} else "mixed")
    return {"samples": samples, "weight": weight, "source_kind": kind}


def merge(
    annotated: list[dict[str, Any]],
    samples: Iterable[SampleRow],
    *,
    restrict_to: list[LoopRegion] | None = None,
) -> list[MergedRecord]:
    """Attach a ``hotness`` block to each annotated entry.

    ``restrict_to``: when provided, only instructions whose address is in
    any of the loops' address sets get hotness attached (others pass through
    with ``hotness={}``).
    """
    by_addr, by_srcline = _group_samples(samples)

    restrict_addrs: set[int] | None = None
    if restrict_to is not None:
        restrict_addrs = set()
        for l in restrict_to:
            restrict_addrs.update(l.addresses)

    # Per-event cumulative weight across everything kept → used for rank.
    event_totals: dict[str, float] = defaultdict(float)
    for s in samples:
        event_totals[s.event] += s.weight

    out: list[MergedRecord] = []
    by_weight: list[tuple[float, MergedRecord]] = []

    for entry in annotated:
        addr_s = entry.get("address")
        addr: int | None
        if isinstance(addr_s, str):
            try:
                addr = int(addr_s, 16) if addr_s.startswith(("0x", "0X")) else int(addr_s)
            except ValueError:
                addr = None
        elif isinstance(addr_s, int):
            addr = addr_s
        else:
            addr = None

        src_file = entry.get("source_file")
        src_line = entry.get("source_line")

        hotness: dict[str, Any] = {}

        # Primary join: address.
        event_buckets: dict[str, list[SampleRow]] = {}
        if addr is not None and addr in by_addr:
            event_buckets = by_addr[addr]
        elif src_file and src_line is not None and (src_file, src_line) in by_srcline:
            event_buckets = by_srcline[(src_file, src_line)]

        if event_buckets:
            for event, rows in event_buckets.items():
                hotness[event] = _summarize_event_bucket(rows)

            if restrict_addrs is not None and addr is not None:
                hotness["in_hot_loop"] = addr in restrict_addrs
            elif restrict_addrs is not None and addr is None:
                hotness["in_hot_loop"] = False

        rec = MergedRecord(
            mnemonic=str(entry.get("mnemonic", "")),
            address=addr,
            source_file=src_file,
            source_line=src_line,
            annotation=entry.get("annotation"),
            hotness=hotness,
            summary=entry.get("summary"),
            known=bool(entry.get("known", True)),
            raw=entry.get("raw"),
        )
        out.append(rec)

        primary_event = next(iter(hotness), None) if hotness else None
        primary_weight = (
            hotness[primary_event]["weight"]
            if primary_event and primary_event not in {"in_hot_loop"}
            else 0.0
        )
        by_weight.append((primary_weight, rec))

    # Assign ranks (1 = hottest) on a per-event basis using the first event.
    by_weight.sort(key=lambda p: -p[0])
    for rank, (w, rec) in enumerate(by_weight, start=1):
        if w > 0:
            rec.hotness["rank"] = rank

    return out


def render_sa(records: list[MergedRecord]) -> str:
    """Render merged records as a side-annotated ``.sa``-style listing."""
    lines: list[str] = []
    for rec in records:
        pct = 0.0
        kind_tag = ""
        if rec.hotness:
            first = next(
                (e for e in rec.hotness if e not in {"rank", "in_hot_loop"}),
                None,
            )
            if first:
                pct = rec.hotness[first].get("weight", 0.0) * 100.0
                kind = rec.hotness[first].get("source_kind", "")
                kind_tag = "[measured]" if kind == "measured" else (f"[{kind}]" if kind else "")
        bar = _bar(pct)
        star = " *" if rec.hotness.get("rank") == 1 else ""
        head = f"{pct:5.1f}% {bar} "
        body = rec.raw or (f"{rec.mnemonic}" + (f"   # {rec.annotation}" if rec.annotation else ""))
        tail = f" {kind_tag}{star}" if kind_tag or star else ""
        lines.append(f"{head}{body}{tail}")
    return "\n".join(lines) + "\n"


def _bar(pct: float) -> str:
    bars = [" ", "▏", "▌", "▐", "█"]
    if pct <= 0:
        return bars[0]
    if pct >= 40:
        return bars[4]
    if pct >= 20:
        return bars[3]
    if pct >= 5:
        return bars[2]
    return bars[1]


def write_merged_json(records: list[MergedRecord], path: Path) -> None:
    path.write_text(
        json.dumps(
            {"schema": "simdref.merged.v1", "records": [r.to_dict() for r in records]}, indent=2
        )
        + "\n"
    )
