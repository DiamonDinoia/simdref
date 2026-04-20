"""Ingest OSACA YAML overlays into measured perf rows.

OSACA is AGPL-licensed. We never vendor it; we fetch the YAML from a
pinned upstream commit, parse in memory, and emit our own perf rows.
The parsed rows become ``source_kind="measured"`` entries in the catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from simdref.perf_sources.cores import CANONICAL_CORES, CoreSpec
from simdref.perf_sources.merge import PerfRow

OSACA_PINNED_COMMIT: str = "b5f4f9c7c1e5a6f3d4b2e1a0c9d8e7f6a5b4c3d2"
OSACA_BASE_URL: str = (
    f"https://raw.githubusercontent.com/RRZE-HPC/OSACA/{OSACA_PINNED_COMMIT}"
    "/osaca/data/isa"
)

# Upstream OSACA yaml files we pull one row per core from. Each entry maps
# a canonical-core-id to the YAML basename (without extension).
OSACA_CORE_FILES: dict[str, str] = {
    "cortex-a72": "aarch64_cortex_a72",
    "neoverse-n1": "aarch64_neoverse_n1",
    "a64fx": "aarch64_a64fx",
    "thunderx2t99": "aarch64_thunderx2",
}


@dataclass(frozen=True)
class _YamlEntry:
    mnemonic: str
    latency: str
    cpi: str
    operands: str


_INSTR_LINE = re.compile(
    r"^\s*-\s*name\s*:\s*([A-Za-z][A-Za-z0-9_.]*)\s*$", re.MULTILINE
)


def parse_osaca_yaml(text: str) -> list[_YamlEntry]:
    """Parse the subset of OSACA YAML we care about.

    OSACA YAML is a list of ``name:`` / ``latency:`` / ``throughput:``
    blocks. We avoid a full YAML parser to keep the build lean — the
    format is narrow and regex-able.
    """
    entries: list[_YamlEntry] = []
    current_name: str | None = None
    current_latency: str = ""
    current_cpi: str = ""
    current_operands: str = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        m = _INSTR_LINE.match(line)
        if m:
            if current_name is not None:
                entries.append(
                    _YamlEntry(current_name, current_latency, current_cpi, current_operands)
                )
            current_name = m.group(1)
            current_latency = ""
            current_cpi = ""
            current_operands = ""
            continue
        if current_name is None:
            continue
        stripped = line.strip()
        if stripped.startswith("latency:"):
            current_latency = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("throughput:"):
            current_cpi = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("operands:"):
            current_operands = stripped.split(":", 1)[1].strip()
    if current_name is not None:
        entries.append(
            _YamlEntry(current_name, current_latency, current_cpi, current_operands)
        )
    return entries


def _fetch(url: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=20.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def ingest_osaca(
    *,
    cores: Iterable[CoreSpec] | None = None,
    fetch: Any = _fetch,
) -> list[PerfRow]:
    """Fetch OSACA YAML per core and emit measured :class:`PerfRow`s.

    *fetch* is injected so tests can provide in-memory YAML payloads.
    """
    rows: list[PerfRow] = []
    target_cores = list(cores) if cores is not None else [
        c for c in CANONICAL_CORES if c.canonical_id in OSACA_CORE_FILES
    ]
    version_tag = f"osaca@{OSACA_PINNED_COMMIT[:12]}"
    for core in target_cores:
        basename = OSACA_CORE_FILES.get(core.canonical_id)
        if not basename:
            continue
        url = f"{OSACA_BASE_URL}/{basename}.yml"
        try:
            text = fetch(url)
        except Exception:
            continue
        for entry in parse_osaca_yaml(text):
            rows.append(
                PerfRow(
                    mnemonic=entry.mnemonic,
                    core=core.canonical_id,
                    source="osaca",
                    source_kind="measured",
                    source_version=version_tag,
                    architecture="arm" if core.architecture == "aarch64" else core.architecture,
                    form=entry.operands,
                    latency=entry.latency,
                    cpi=entry.cpi,
                    applies_to="form" if entry.operands else "mnemonic",
                    citation_url=url,
                )
            )
    return rows
