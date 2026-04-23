"""AMD uProf adapter (CSV export)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from simdref.profile.model import SampleRow
from simdref.profile.registry import register_profiler


class _UprofAdapter:
    id = "uprof"
    description = "AMD uProf instruction-level CSV"

    def can_handle(self, path: Path) -> bool:
        if not path.exists() or path.is_dir() or path.suffix.lower() != ".csv":
            return False
        try:
            head = path.read_text(errors="replace").splitlines()[:2]
        except OSError:
            return False
        if not head:
            return False
        return "Offset" in head[0] or "Instruction RIP" in head[0] or "Samples" in head[0]

    def ingest(self, path: Path, *, binary: Path | None) -> Iterable[SampleRow]:
        with path.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return
        # Column name hunt — uProf exports vary by version.
        addr_col = next(
            (c for c in rows[0] if c.lower() in ("instruction rip", "offset", "address")),
            None,
        )
        samples_col = next(
            (c for c in rows[0] if "samples" in c.lower() or "ir" in c.lower()),
            None,
        )
        if addr_col is None or samples_col is None:
            return
        sym_col = next((c for c in rows[0] if c.lower() in ("function", "symbol")), None)
        file_col = next(
            (c for c in rows[0] if c.lower() in ("source file", "file", "source")),
            None,
        )
        line_col = next(
            (c for c in rows[0] if c.lower() in ("source line", "line")),
            None,
        )

        parsed: list[tuple[int, int, str, str | None, int | None]] = []
        total = 0
        for r in rows:
            try:
                addr_s = r[addr_col].strip()
                addr = int(addr_s, 16) if addr_s.startswith(("0x", "0X")) else int(addr_s, 16)
            except (ValueError, KeyError):
                continue
            try:
                samples = int(float(r[samples_col]))
            except (ValueError, KeyError):
                continue
            sym = (r.get(sym_col or "") or "").strip() if sym_col else ""
            src = (r.get(file_col or "") or "").strip() if file_col else ""
            try:
                ln = int(r.get(line_col or "") or "") if line_col else None
            except ValueError:
                ln = None
            parsed.append((addr, samples, sym, src or None, ln))
            total += samples

        total = total or 1
        for addr, samples, sym, src, ln in parsed:
            yield SampleRow(
                address=addr,
                event="uprof:samples",
                samples=samples,
                weight=samples / total,
                symbol=sym,
                source_file=src,
                source_line=ln,
            )


register_profiler(_UprofAdapter())
