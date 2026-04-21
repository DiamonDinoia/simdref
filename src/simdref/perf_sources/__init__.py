"""Ingesters for measured and modeled microarchitectural perf data.

Public surface:

- :func:`ingest_llvm_mca` — drives the llvm-exegesis → llvm-mc →
  llvm-mca pipeline per canonical core and returns modeled
  :class:`PerfRow` s.
- :func:`collect_core_schedule` — lower-level single-core entry point.
- :func:`merge_perf_rows` — attaches produced rows to an existing catalog.
- :data:`CANONICAL_CORES` — name-map from upstream core ids → stable ids.

Per-instruction measured data for RISC-V RVV is not currently available
from any public upstream: ``rvv-bench-results`` publishes kernel-level
benchmarks (memcpy, chacha20, etc.), not instruction tables. RISC-V
per-core rows therefore come from llvm-mca scheduling models only.
"""

from simdref.perf_sources.cores import (
    CANONICAL_CORES,
    canonical_core_id,
    core_architecture,
)
from simdref.perf_sources.llvm_mca import (
    LLVM_MCA_MIN_VERSION,
    LLVMMcaError,
    LLVMMcaUnavailable,
    detect_llvm_mca_version,
    ingest_llvm_mca,
    parse_llvm_mca_json,
)
from simdref.perf_sources.llvm_scheduling import (
    LLVMSchedulingError,
    collect_core_schedule,
)
from simdref.perf_sources.merge import PerfRow, merge_perf_rows

__all__ = [
    "CANONICAL_CORES",
    "canonical_core_id",
    "core_architecture",
    "PerfRow",
    "merge_perf_rows",
    "LLVM_MCA_MIN_VERSION",
    "LLVMMcaError",
    "LLVMMcaUnavailable",
    "LLVMSchedulingError",
    "detect_llvm_mca_version",
    "ingest_llvm_mca",
    "parse_llvm_mca_json",
    "collect_core_schedule",
]
