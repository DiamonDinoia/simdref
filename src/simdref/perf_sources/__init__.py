"""Ingesters for measured and modeled microarchitectural perf data.

These ingesters produce rows that get merged into
``InstructionRecord.arch_details[<core>]`` entries, each stamped with a
``source``, ``source_kind`` (``"measured"`` / ``"modeled"``),
``source_version``, ``applies_to``, and ``citation_url`` so renderers can
always show provenance.

Public surface:

- :func:`ingest_llvm_mca` — drives ``llvm-mca --json`` per ``(triple, cpu)``
- :func:`ingest_osaca`    — fetches OSACA YAML (AGPL, fetch-only) and parses
- :func:`ingest_rvv_bench` — fetches rvv-bench-results JSON
- :func:`merge_perf_rows` — attaches produced rows to an existing catalog
- :data:`CANONICAL_CORES` — name-map from upstream core ids → stable ids
"""

from simdref.perf_sources.cores import (
    CANONICAL_CORES,
    canonical_core_id,
    core_architecture,
)
from simdref.perf_sources.merge import PerfRow, merge_perf_rows
from simdref.perf_sources.llvm_mca import (
    LLVM_MCA_MIN_VERSION,
    LLVMMcaUnavailable,
    detect_llvm_mca_version,
    ingest_llvm_mca,
    parse_llvm_mca_json,
)
from simdref.perf_sources.osaca import (
    OSACA_PINNED_COMMIT,
    ingest_osaca,
    parse_osaca_yaml,
)
from simdref.perf_sources.rvv_bench import (
    RVV_BENCH_PINNED_COMMIT,
    ingest_rvv_bench,
    parse_rvv_bench_json,
)

__all__ = [
    "CANONICAL_CORES",
    "canonical_core_id",
    "core_architecture",
    "PerfRow",
    "merge_perf_rows",
    "LLVM_MCA_MIN_VERSION",
    "LLVMMcaUnavailable",
    "detect_llvm_mca_version",
    "ingest_llvm_mca",
    "parse_llvm_mca_json",
    "OSACA_PINNED_COMMIT",
    "ingest_osaca",
    "parse_osaca_yaml",
    "RVV_BENCH_PINNED_COMMIT",
    "ingest_rvv_bench",
    "parse_rvv_bench_json",
]
