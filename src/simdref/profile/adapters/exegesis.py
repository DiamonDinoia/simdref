"""``llvm-exegesis`` adapter (per-instruction measured latency).

Consumes ``llvm-exegesis --mode=latency`` JSON output. These are
measurements of individual instruction latencies on the current CPU — not a
trace — but they still fit the ``SampleRow`` shape (one row per
instruction, tagged ``source_kind="measured"``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from simdref.profile.model import SampleRow
from simdref.profile.registry import register_profiler


class _ExegesisAdapter:
    id = "exegesis"
    description = "llvm-exegesis per-instruction latency JSON"

    def can_handle(self, path: Path) -> bool:
        if not path.exists() or path.is_dir():
            return False
        try:
            head = path.read_text(errors="replace")[:2048]
        except OSError:
            return False
        return "exegesis" in head.lower() or "\"mode\": \"latency\"" in head

    def ingest(self, path: Path, *, binary: Path | None) -> Iterable[SampleRow]:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return
        entries = data if isinstance(data, list) else data.get("results", [])
        total = 0.0
        parsed: list[tuple[str, float]] = []
        for e in entries:
            mnem = e.get("instruction") or e.get("key", {}).get("instruction") or ""
            measurements = e.get("measurements") or []
            value = 0.0
            for m in measurements:
                if m.get("key") in ("latency", "inverse_throughput"):
                    try:
                        value = float(m.get("value", 0.0))
                    except (TypeError, ValueError):
                        value = 0.0
                    break
            parsed.append((mnem, value))
            total += value
        total = total or 1.0
        for idx, (mnem, value) in enumerate(parsed):
            yield SampleRow(
                address=0x1000 + idx * 4,
                event="exegesis:latency",
                samples=int(round(value * 100)),
                weight=value / total,
                symbol=mnem,
                source_kind="measured",
            )


register_profiler(_ExegesisAdapter())
