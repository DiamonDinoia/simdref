"""Static web-app export for simdref.

Generates a self-contained single-page application (``index.html``) plus a
two-tier data split:

* **search-index.json** -- compact search data loaded at startup (~400 KB gzipped).
* **detail-chunks/{PREFIX}.json** -- full instruction details (operands,
  measurements) loaded on demand when the user selects a result.

The HTML template is assembled from three source files under
``simdref/templates/`` (HTML shell, CSS, JS) and inlined into a single
``index.html``.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import asdict
from importlib import resources
from pathlib import Path

from simdref.models import Catalog
from simdref.perf import best_numeric, latency_cycle_values, variant_perf_summary


def _load_template() -> str:
    """Assemble the HTML template from shell + CSS + JS source files."""
    tpl = resources.files("simdref.templates")
    html = tpl.joinpath("index.html").read_text()
    css = tpl.joinpath("style.css").read_text()
    js = tpl.joinpath("app.js").read_text()
    return html.replace("/* __CSS__ */", css).replace("/* __JS__ */", js)


def _latency_value(latencies: list[dict]) -> str:
    """Best latency value from a list of latency dicts."""
    return best_numeric(latency_cycle_values(latencies))


def _web_measurements(item) -> list[dict]:
    """Per-microarchitecture measurement rows for the web UI."""
    rows: list[dict] = []
    for uarch, details in item.arch_details.items():
        measurement = details.get("measurement") or {}
        if not measurement:
            continue
        rows.append({
            "uarch": uarch,
            "ports": measurement.get("ports", "-"),
            "latency": _latency_value(details.get("latencies") or []),
            "tpLoop": measurement.get("TP_loop") or measurement.get("TP") or "-",
            "tpUnrolled": measurement.get("TP_unrolled", "-"),
            "tpPorts": measurement.get("TP_ports", "-"),
            "uops": measurement.get("uops", "-"),
        })
    return rows


def _truncate(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "\u2026"


def _chunk_prefix(mnemonic: str) -> str:
    """3-char uppercase prefix for grouping instructions into chunks."""
    clean = mnemonic.strip().upper()
    return clean[:3] if len(clean) >= 3 else clean


def _search_payload(catalog: Catalog) -> dict:
    """Compact search-only payload for fast initial load."""
    # Pre-compute perf summaries for instructions (keyed by instruction key).
    instr_perf: dict[str, tuple[str, str]] = {}
    instr_by_key: dict[str, object] = {}
    for item in catalog.instructions:
        lat, cpi = variant_perf_summary(item.arch_details)
        instr_perf[item.key] = (lat, cpi)
        instr_by_key[item.key] = item

    def _intrinsic_perf(item) -> tuple[str, str]:
        """Best lat/cpi from the primary linked instruction."""
        for ref in item.instructions:
            if ref in instr_perf:
                return instr_perf[ref]
        return ("-", "-")

    return {
        "generated_at": catalog.generated_at,
        "sources": [asdict(source) for source in catalog.sources],
        "intrinsics": [
            {
                "name": item.name,
                "signature": _truncate(item.signature),
                "description": _truncate(item.description),
                "header": item.header,
                "isa": item.isa,
                "category": item.category,
                "instructions": item.instructions,
                "notes": item.notes,
                "lat": _intrinsic_perf(item)[0],
                "cpi": _intrinsic_perf(item)[1],
            }
            for item in catalog.intrinsics
        ],
        "instructions": [
            {
                "key": item.key,
                "mnemonic": item.mnemonic,
                "form": item.form,
                "summary": _truncate(item.summary),
                "isa": item.isa,
                "linked_intrinsics": item.linked_intrinsics,
                "lat": instr_perf[item.key][0],
                "cpi": instr_perf[item.key][1],
            }
            for item in catalog.instructions
        ],
    }


def _detail_chunks(catalog: Catalog) -> dict[str, dict]:
    """Group full instruction details by 3-char mnemonic prefix.

    Returns a mapping of ``{prefix: {key: detail_dict}}``.
    """
    chunks: dict[str, dict] = defaultdict(dict)
    for item in catalog.instructions:
        prefix = _chunk_prefix(item.mnemonic)
        chunks[prefix][item.key] = {
            "mnemonic": item.mnemonic,
            "form": item.form,
            "summary": item.summary,
            "isa": item.isa,
            "operand_details": [
                {
                    "idx": op.get("idx", ""),
                    "r": op.get("r", ""),
                    "w": op.get("w", ""),
                    "type": op.get("type", ""),
                    "width": op.get("width", ""),
                    "xtype": op.get("xtype", ""),
                    "name": op.get("name", ""),
                }
                for op in item.operand_details
            ],
            "metadata": {
                k: v for k, v in item.metadata.items()
                if k in {"url", "url-ref", "category", "cpl"}
            },
            "linked_intrinsics": item.linked_intrinsics,
            "measurements": _web_measurements(item),
        }
    return dict(chunks)


def _intrinsic_details(catalog: Catalog) -> dict[str, dict]:
    """Full intrinsic details keyed by name (for on-demand loading)."""
    return {
        item.name: {
            "name": item.name,
            "signature": item.signature,
            "description": item.description,
            "header": item.header,
            "isa": item.isa,
            "category": item.category,
            "instructions": item.instructions,
            "notes": item.notes,
        }
        for item in catalog.intrinsics
    }


def export_web(catalog: Catalog, web_dir: Path) -> None:
    """Write the web app to *web_dir*.

    Outputs:
    * ``index.html`` -- assembled SPA
    * ``search-index.json`` -- compact search data
    * ``detail-chunks/{PREFIX}.json`` -- instruction detail chunks
    * ``intrinsic-details.json`` -- full intrinsic details
    """
    web_dir.mkdir(parents=True, exist_ok=True)

    # Assembled HTML
    (web_dir / "index.html").write_text(_load_template())

    # Tier 1: search index
    (web_dir / "search-index.json").write_text(
        json.dumps(_search_payload(catalog), separators=(",", ":"), sort_keys=True)
    )

    # Tier 2: detail chunks
    chunks_dir = web_dir / "detail-chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    chunks_dir.mkdir()
    for prefix, chunk in _detail_chunks(catalog).items():
        (chunks_dir / f"{prefix}.json").write_text(
            json.dumps(chunk, separators=(",", ":"), sort_keys=True)
        )

    # Intrinsic details (single file, small enough)
    (web_dir / "intrinsic-details.json").write_text(
        json.dumps(_intrinsic_details(catalog), separators=(",", ":"), sort_keys=True)
    )
