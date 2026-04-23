"""Apple ``xctrace`` / Instruments adapter (XML export).

Darwin-only; the adapter still registers on other platforms so
``iter_profilers()`` lists it, but ``can_handle`` returns False unless the
input is a plausible xctrace XML export.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from simdref.profile.model import SampleRow
from simdref.profile.registry import register_profiler


class _XctraceAdapter:
    id = "xctrace"
    description = "Apple Instruments xctrace XML export"

    def can_handle(self, path: Path) -> bool:
        if not path.exists() or path.is_dir() or path.suffix.lower() != ".xml":
            return False
        try:
            head = path.read_text(errors="replace")[:2048]
        except OSError:
            return False
        return "<trace-query-result" in head or "xctrace" in head.lower()

    def ingest(self, path: Path, *, binary: Path | None) -> Iterable[SampleRow]:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return
        # xctrace XML: rows contain ``<row>`` with sample address + weight.
        rows = list(root.iter("row")) or list(root.iter("sample"))
        parsed: list[tuple[int, int, str]] = []
        total = 0
        for row in rows:
            addr_node = row.find("address") if row.find("address") is not None else row.find("ip")
            weight_node = row.find("weight") or row.find("count") or row.find("samples")
            sym_node = row.find("symbol") or row.find("function")
            if addr_node is None or weight_node is None:
                continue
            try:
                addr_text = (addr_node.text or "").strip()
                addr = int(addr_text, 16) if addr_text.startswith(("0x", "0X")) else int(addr_text)
                samples = int((weight_node.text or "0").strip())
            except ValueError:
                continue
            sym = (sym_node.text if sym_node is not None else "") or ""
            parsed.append((addr, samples, sym))
            total += samples
        total = total or 1
        for addr, samples, sym in parsed:
            yield SampleRow(
                address=addr,
                event="xctrace:cpu",
                samples=samples,
                weight=samples / total,
                symbol=sym,
            )


register_profiler(_XctraceAdapter())
