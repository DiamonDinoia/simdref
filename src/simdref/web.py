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

import gzip
import json
import re
import shutil
from collections import defaultdict
from dataclasses import asdict
from importlib import resources
from pathlib import Path

from simdref import __version__
from simdref.filters import FilterSpec, CategorySpec
from simdref.ui_labels import as_json_dict as _ui_labels_payload
from simdref.models import Catalog
from simdref.display import (
    display_architecture,
    display_isa,
    display_instruction_form,
    isa_families,
    isa_family,
    isa_to_sub_isa,
    strip_instruction_decorators,
)
from simdref.perf import best_numeric, latency_cycle_values, variant_perf_summary
from simdref.pdfrefs import normalize_pdf_refs
from simdref.storage import derive_arm_arch


def _load_template() -> str:
    """Assemble the HTML template from shell + CSS + JS source files.

    Injects ``UI_LABELS`` + ``KEYMAP`` as a ``window.SIMDREF_UI`` JSON blob
    before the app script so the SPA and TUI share the same vocabulary.
    """
    tpl = resources.files("simdref.templates")
    html = tpl.joinpath("index.html").read_text()
    css = tpl.joinpath("style.css").read_text()
    js = tpl.joinpath("app.js").read_text()
    ui_blob = json.dumps(_ui_labels_payload(), separators=(",", ":"))
    js_with_labels = f"window.SIMDREF_UI = {ui_blob};\n{js}"
    return html.replace("/* __CSS__ */", css).replace("/* __JS__ */", js_with_labels)


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
        rows.append(
            {
                "uarch": uarch,
                "ports": measurement.get("ports", "-"),
                "latency": _latency_value(details.get("latencies") or []),
                "tpLoop": measurement.get("TP_loop") or measurement.get("TP") or "-",
                "tpUnrolled": measurement.get("TP_unrolled", "-"),
                "tpPorts": measurement.get("TP_ports", "-"),
                "uops": measurement.get("uops", "-"),
                "sourceKind": details.get("source_kind") or "measured",
            }
        )
    return rows


def _truncate(text: str, max_len: int = 120) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _chunk_prefix(mnemonic: str) -> str:
    """3-char uppercase prefix for grouping instructions into chunks."""
    clean = mnemonic.strip().upper()
    return clean[:3] if len(clean) >= 3 else clean


_INTRINSIC_PREFIX_STRIP = re.compile(r"^(?:mm\d*|sv|vq?)_?", re.IGNORECASE)
_INTRINSIC_ALNUM = re.compile(r"[A-Za-z0-9]")


def _intrinsic_chunk_prefix(name: str) -> str:
    """3-char bucket key for grouping intrinsics by their op name.

    Strips leading underscores, an optional ``riscv_`` namespace, and a
    single width/family token (``mm``, ``mm256``, ``mm512``, ``sv``,
    ``v``, ``vq``) so that buckets cluster by operation (``add``,
    ``cvt``, ``fma``) rather than by vector width or platform. Without
    the ``riscv_`` strip the 74k RISC-V vector intrinsics all fall into
    a single 89 MB bucket.

    Falls back to ``misc`` when a name does not produce at least two
    alphanumeric characters after stripping.

    The same function is re-implemented in ``app.js`` — keep in sync.
    """
    s = name.lstrip("_")
    if s[:6].lower() == "riscv_":
        s = s[6:]
    m = _INTRINSIC_PREFIX_STRIP.match(s)
    if m and m.end() < len(s):
        s = s[m.end() :]
    s = s.lstrip("_")
    clean = "".join(_INTRINSIC_ALNUM.findall(s)).lower()
    if len(clean) < 2:
        return "misc"
    return clean[:3]


def _intrinsic_search_fields(item) -> list[str]:
    """Slim search-only field set: only what ranking/scoring actually needs."""
    return [
        item.name,
        item.description or "",
        display_isa(item.isa),
        " ".join(item.instructions or []),
    ]


def _instruction_search_fields(item) -> list[str]:
    display_key = display_instruction_form(item.key)
    display_form = display_instruction_form(item.form)
    return [
        strip_instruction_decorators(item.mnemonic or ""),
        display_key,
        display_form,
        item.summary or "",
        display_isa(item.isa),
    ]


def _filter_spec_for_catalog(catalog: Catalog) -> FilterSpec:
    spec = FilterSpec()
    aggregate: dict[tuple[str, str, str], int] = {}
    for item in catalog.intrinsics:
        if not item.category:
            continue
        families = {isa_family(v) for v in (item.isa or [])} or {"Other"}
        for family in families:
            key = (family, item.category, item.subcategory or "")
            aggregate[key] = aggregate.get(key, 0) + 1
    for item in catalog.instructions:
        cat = (item.metadata or {}).get("category", "") if isinstance(item.metadata, dict) else ""
        if not cat:
            continue
        families = {isa_family(v) for v in (item.isa or [])} or {"Other"}
        for family in families:
            key = (family, cat, "")
            aggregate[key] = aggregate.get(key, 0) + 1
    spec.categories = [
        CategorySpec(family=fam, category=cat, subcategory=sub, count=n)
        for (fam, cat, sub), n in sorted(aggregate.items())
    ]
    return spec


def _isa_config() -> dict:
    """Legacy isa_config block embedded in ``search-index.json``."""
    payload = FilterSpec().to_json()
    payload.pop("categories", None)
    return payload


def _search_payload(catalog: Catalog) -> dict:
    """Compact search-only payload for fast initial load."""
    # Pre-compute perf summaries for instructions (keyed by instruction key).
    instr_perf: dict[str, tuple[str, str]] = {}
    for item in catalog.instructions:
        lat, cpi = variant_perf_summary(item.arch_details)
        instr_perf[item.db_key] = (lat, cpi)

    def _intrinsic_perf(item) -> tuple[str, str]:
        """Best lat/cpi from the primary linked instruction."""
        for inst_ref in item.instruction_refs:
            key = inst_ref.get("key", "")
            if key in instr_perf:
                return instr_perf[key]
        return ("-", "-")

    def _intrinsic_primary_instr(item) -> str:
        for ref in item.instruction_refs or []:
            key = ref.get("key") if isinstance(ref, dict) else None
            if key:
                return key
        return (item.instructions or [""])[0]

    intrinsics_out = []
    for item in catalog.intrinsics:
        lat, cpi = _intrinsic_perf(item)
        fields = _intrinsic_search_fields(item)
        entry: dict = {
            "name": item.name,
            "subtitle": _truncate(item.description or "", 80),
            "architecture": item.architecture,
            "isa": item.isa,
            "lat": lat,
            "cpi": cpi,
            "display_architecture": display_architecture(item.architecture),
            "display_isa": display_isa(item.isa),
            "isa_families": isa_families(item.isa),
            "isa_subs": list(
                dict.fromkeys(filter(None, (isa_to_sub_isa(value) for value in item.isa)))
            ),
            "search_fields": fields,
        }
        primary = _intrinsic_primary_instr(item)
        if primary:
            entry["primary_instr"] = primary
        arm_arch = derive_arm_arch(
            item.isa, item.metadata if isinstance(item.metadata, dict) else {}
        )
        if arm_arch:
            entry["arm_arch"] = arm_arch
        category = (
            (item.metadata or {}).get("category", "") if isinstance(item.metadata, dict) else ""
        )
        if category:
            entry["category"] = category
        intrinsics_out.append(entry)

    instructions_out = []
    for item in catalog.instructions:
        lat, cpi = instr_perf[item.db_key]
        fields = _instruction_search_fields(item)
        instructions_out.append(
            {
                "key": item.db_key,
                "mnemonic": item.mnemonic,
                "form": item.form,
                "architecture": item.architecture,
                "summary": _truncate(item.summary or "", 80),
                "isa": item.isa,
                "linked_intrinsics": item.linked_intrinsics,
                "lat": lat,
                "cpi": cpi,
                "display_architecture": display_architecture(item.architecture),
                "display_key": display_instruction_form(item.key),
                "display_form": display_instruction_form(item.form),
                "display_mnemonic": strip_instruction_decorators(item.mnemonic),
                "display_isa": display_isa(item.isa),
                "isa_families": isa_families(item.isa),
                "isa_subs": list(
                    dict.fromkeys(filter(None, (isa_to_sub_isa(value) for value in item.isa)))
                ),
                "search_fields": fields,
            }
        )

    return {
        "generated_at": catalog.generated_at,
        "sources": [asdict(source) for source in catalog.sources],
        "isa_config": _isa_config(),
        "intrinsics": intrinsics_out,
        "instructions": instructions_out,
    }


def _detail_chunks(catalog: Catalog) -> dict[str, dict]:
    """Group full instruction details by 3-char mnemonic prefix.

    Returns a mapping of ``{prefix: {key: detail_dict}}``.
    """
    chunks: dict[str, dict] = defaultdict(dict)
    for item in catalog.instructions:
        prefix = _chunk_prefix(item.mnemonic)
        chunks[prefix][item.db_key] = {
            "mnemonic": item.mnemonic,
            "form": item.form,
            "architecture": item.architecture,
            "display_architecture": display_architecture(item.architecture),
            "display_form": display_instruction_form(item.form),
            "display_mnemonic": strip_instruction_decorators(item.mnemonic),
            "summary": item.summary,
            "description": item.description,
            "isa": item.isa,
            "display_isa": display_isa(item.isa),
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
                k: v
                for k, v in item.metadata.items()
                if k
                in {
                    "url",
                    "url-ref",
                    "category",
                    "cpl",
                    "intel-sdm-url",
                    "intel-sdm-page-start",
                    "intel-sdm-page-end",
                }
            },
            "pdf_refs": normalize_pdf_refs(item.pdf_refs, item.metadata),
            "linked_intrinsics": item.linked_intrinsics,
            "measurements": _web_measurements(item),
        }
    return dict(chunks)


def _intrinsic_detail_entry(item) -> dict:
    return {
        "name": item.name,
        "signature": item.signature,
        "description": item.description,
        "header": item.header,
        "url": item.url,
        "architecture": item.architecture,
        "display_architecture": display_architecture(item.architecture),
        "isa": item.isa,
        "display_isa": display_isa(item.isa),
        "display_isa_tokens": [display_isa([value]) for value in item.isa],
        "instructions": item.instructions,
        "instruction_refs": item.instruction_refs,
        "metadata": item.metadata,
        "doc_sections": item.doc_sections,
        "notes": item.notes,
    }


def _intrinsic_chunks(catalog: Catalog) -> dict[str, dict]:
    """Group full intrinsic details by bucket prefix.

    Mirrors the ``detail-chunks/`` layout used for instructions so that
    clicking an intrinsic only fetches the ~100-200 KB chunk containing
    it rather than the ~138 MB full file.
    """
    chunks: dict[str, dict] = defaultdict(dict)
    for item in catalog.intrinsics:
        prefix = _intrinsic_chunk_prefix(item.name)
        chunks[prefix][item.name] = _intrinsic_detail_entry(item)
    return dict(chunks)


def _build_stamp(catalog: Catalog) -> dict:
    from datetime import datetime, timezone
    import subprocess

    git_sha = ""
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        git_sha = ""
    return {
        "version": __version__,
        "git_sha": git_sha,
        "catalog_generated_at": catalog.generated_at,
        "intrinsics": len(catalog.intrinsics),
        "instructions": len(catalog.instructions),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def export_web(catalog: Catalog, web_dir: Path) -> None:
    """Write the web app to *web_dir*.

    Outputs:
    * ``index.html`` -- assembled SPA
    * ``search-index.json`` -- compact search data
    * ``filter_spec.json`` -- shared ISA/category facets (web + CLI)
    * ``build_stamp.json`` -- version/freshness metadata
    * ``detail-chunks/{PREFIX}.json`` -- instruction detail chunks
    * ``intrinsic-chunks/{PREFIX}.json`` -- intrinsic detail chunks
    """
    web_dir.mkdir(parents=True, exist_ok=True)

    def _write_json(path: Path, payload) -> None:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        path.write_bytes(raw)
        with gzip.open(str(path) + ".gz", "wb", compresslevel=6) as fh:
            fh.write(raw)

    (web_dir / "index.html").write_text(_load_template())

    _write_json(web_dir / "search-index.json", _search_payload(catalog))

    filter_spec = _filter_spec_for_catalog(catalog)
    _write_json(web_dir / "filter_spec.json", filter_spec.to_json())

    _write_json(web_dir / "build_stamp.json", _build_stamp(catalog))

    chunks_dir = web_dir / "detail-chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    chunks_dir.mkdir()
    for prefix, chunk in _detail_chunks(catalog).items():
        _write_json(chunks_dir / f"{prefix}.json", chunk)

    intrinsic_chunks_dir = web_dir / "intrinsic-chunks"
    if intrinsic_chunks_dir.exists():
        shutil.rmtree(intrinsic_chunks_dir)
    intrinsic_chunks_dir.mkdir()
    for prefix, chunk in _intrinsic_chunks(catalog).items():
        _write_json(intrinsic_chunks_dir / f"{prefix}.json", chunk)

    for stale in (web_dir / "intrinsic-details.json", web_dir / "intrinsic-details.json.gz"):
        if stale.exists():
            stale.unlink()
