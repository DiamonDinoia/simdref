"""Runtime profile ingestion and hot-loop detection for simdref.

Runtime samples (``SampleRow``) are intentionally kept distinct from the
catalog-side ``PerfRow`` used by ``simdref.perf_sources``: catalog data is
static per-mnemonic cost, samples are per-address observed hotness from a
single run. Keeping them separate prevents runtime noise from polluting the
static latency/CPI catalog.
"""

from __future__ import annotations

from simdref.profile.model import LoopRegion, SampleRow
from simdref.profile.registry import (
    get_profiler,
    iter_profilers,
    register_profiler,
)

__all__ = [
    "LoopRegion",
    "SampleRow",
    "get_profiler",
    "iter_profilers",
    "register_profiler",
]
