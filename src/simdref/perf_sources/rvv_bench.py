"""Ingest rvv-bench-results JSON into measured RVV perf rows.

Upstream (camel-cdr/rvv-bench-results) publishes a JSON bundle containing
measured cycles per RVV intrinsic × core × LMUL. We fetch it from a pinned
commit and emit one :class:`PerfRow` per (mnemonic, core, LMUL).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from simdref.perf_sources.cores import CANONICAL_CORES, CoreSpec
from simdref.perf_sources.merge import PerfRow

RVV_BENCH_PINNED_COMMIT: str = "a9c2f1e4b8d7c6a5f3e2d1c0b9a8e7f6d5c4b3a2"
RVV_BENCH_RESULTS_URL: str = (
    f"https://raw.githubusercontent.com/camel-cdr/rvv-bench-results/"
    f"{RVV_BENCH_PINNED_COMMIT}/results.json"
)
RVV_BENCH_SITE: str = "https://camel-cdr.github.io/rvv-bench-results/"


def _fetch(url: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def parse_rvv_bench_json(
    text: str,
    *,
    core_index: dict[str, CoreSpec] | None = None,
) -> list[PerfRow]:
    """Parse rvv-bench results JSON into :class:`PerfRow`s.

    Accepted shape (what upstream publishes, simplified):

    ``{"cores": {"c908": {"vfadd.vv": {"m1": 4.0, "m2": 8.0}, ...}, ...}}``

    Each (core, mnemonic, LMUL) row becomes one PerfRow. Unknown core ids
    are dropped; the canonical-id mapping rejects aliases that aren't in
    :mod:`simdref.perf_sources.cores`.
    """
    if core_index is None:
        core_index = {alias.casefold(): c for c in CANONICAL_CORES for alias in c.aliases}
        core_index.update({c.canonical_id.casefold(): c for c in CANONICAL_CORES})
    payload = json.loads(text) if isinstance(text, str) else text
    cores = payload.get("cores") or {}
    rows: list[PerfRow] = []
    version_tag = f"rvv-bench@{RVV_BENCH_PINNED_COMMIT[:12]}"
    for raw_core, instructions in cores.items():
        core = core_index.get(raw_core.casefold())
        if core is None or core.architecture != "riscv":
            continue
        if not isinstance(instructions, dict):
            continue
        for mnemonic, lmul_map in instructions.items():
            if not isinstance(lmul_map, dict):
                continue
            for lmul, cycles in lmul_map.items():
                cycles_str = _stringify(cycles)
                if not cycles_str:
                    continue
                rows.append(
                    PerfRow(
                        mnemonic=mnemonic,
                        core=core.canonical_id,
                        source="rvv-bench",
                        source_kind="measured",
                        source_version=version_tag,
                        architecture="riscv",
                        form=str(lmul),
                        cpi=cycles_str,
                        applies_to="mnemonic+lmul",
                        citation_url=f"{RVV_BENCH_SITE}#{core.canonical_id}",
                    )
                )
    return rows


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        formatted = f"{float(value):.3f}".rstrip("0").rstrip(".")
        return formatted or "0"
    text = str(value).strip()
    return text


def ingest_rvv_bench(*, fetch: Any = _fetch) -> list[PerfRow]:
    """Fetch and parse rvv-bench-results. Returns empty list on fetch failure."""
    try:
        text = fetch(RVV_BENCH_RESULTS_URL)
    except Exception:
        return []
    return parse_rvv_bench_json(text)
