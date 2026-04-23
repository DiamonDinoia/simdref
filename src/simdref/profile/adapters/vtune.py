"""Intel VTune adapter (CSV export).

Ingests ``vtune -report hotspots -format csv`` output. VTune does not
naturally expose raw VAs in its default hotspots report; we key on
``(source_file, source_line)`` and leave ``address`` synthesized.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from simdref.profile.model import SampleRow
from simdref.profile.registry import register_profiler


_CPU_COL_CANDIDATES = ("CPU Time", "Clockticks", "CPU Time:Self", "Hardware Event Count")


def _pick_col(header: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = [h.strip() for h in header]
    for c in candidates:
        if c in lower:
            return c
    return None


class _VtuneAdapter:
    id = "vtune"
    description = "Intel VTune hotspots CSV"

    def can_handle(self, path: Path) -> bool:
        if not path.exists() or path.is_dir() or path.suffix.lower() != ".csv":
            return False
        try:
            head = path.read_text(errors="replace").splitlines()[:1]
        except OSError:
            return False
        if not head:
            return False
        return "Function" in head[0] and any(c in head[0] for c in _CPU_COL_CANDIDATES)

    def ingest(self, path: Path, *, binary: Path | None) -> Iterable[SampleRow]:
        with path.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return
        header = list(rows[0].keys())
        cpu_col = _pick_col(header, _CPU_COL_CANDIDATES)
        if cpu_col is None:
            return
        total = 0.0
        parsed: list[tuple[str, str, int | None, float]] = []
        for r in rows:
            try:
                weight = float(r[cpu_col])
            except (TypeError, ValueError):
                continue
            func = r.get("Function") or r.get("Function / Call Stack") or ""
            src = r.get("Source File") or r.get("Source Full Path") or ""
            ln_s = r.get("Source Line") or r.get("Line") or ""
            try:
                ln = int(ln_s) if ln_s else None
            except ValueError:
                ln = None
            parsed.append((func, src or None, ln, weight))  # type: ignore[arg-type]
            total += weight
        total = total or 1.0
        for idx, (func, src, ln, weight) in enumerate(parsed):
            yield SampleRow(
                address=0x1000 + idx * 4,
                event="vtune:cpu_time",
                samples=int(round(weight * 1000)),
                weight=weight / total,
                symbol=func,
                source_file=src,
                source_line=ln,
            )


register_profiler(_VtuneAdapter())
