"""Public ingest entrypoints and compatibility wrappers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

import msgpack

from simdref.ingest_catalog import (
    _instruction_summary,
    _normalize_operand_xtype,
    build_catalog,
    link_records,
    parse_intel_payload,
    parse_riscv_instruction_payload,
    parse_riscv_intrinsics_payload,
    parse_uops_xml,
)
from simdref.ingest_pdf import merge_pdf_enrichment
from simdref.ingest_sources import (
    fetch_intel_data,
    fetch_riscv_rvv_intrinsics_data,
    fetch_riscv_unified_db_data,
    fetch_uops_xml,
    now_iso,
)
from simdref.pdfparse.intel import INTEL_SDM_CACHE_PATH, INTEL_SDM_URL, parse_intel_sdm
from simdref.storage import ensure_dir


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_or_parse_intel_sdm(
    pdf_path: Path,
    *,
    status: Callable[[str], None] | None = None,
    cache_path: Path = INTEL_SDM_CACHE_PATH,
) -> dict[str, dict[str, object]]:
    if cache_path.exists():
        try:
            payload = msgpack.unpackb(cache_path.read_bytes(), raw=False)
            if payload.get("pdf_url") == INTEL_SDM_URL and payload.get("pdf_sha256") == _sha256_file(pdf_path):
                descriptions = payload.get("descriptions")
                if isinstance(descriptions, dict):
                    return descriptions
        except Exception:
            pass
    descriptions = parse_intel_sdm(pdf_path, status=status)
    if hasattr(descriptions, "descriptions"):
        descriptions = {
            key: {
                "sections": value.sections,
                "source_url": value.source_url,
                "page_start": value.page_start,
                "page_end": value.page_end,
            }
            for key, value in descriptions.descriptions.items()
        }
    ensure_dir(cache_path.parent)
    payload = {
        "pdf_url": INTEL_SDM_URL,
        "pdf_sha256": _sha256_file(pdf_path),
        "descriptions": descriptions,
    }
    cache_path.write_bytes(msgpack.packb(payload, use_bin_type=True))
    return descriptions


def _merge_descriptions(
    instructions,
    descriptions: dict[str, dict[str, object]],
) -> None:
    from simdref.pdfparse.types import PdfDescriptionPayload, PdfEnrichmentResult

    result = PdfEnrichmentResult(
        descriptions={
            key: PdfDescriptionPayload(
                sections=dict(value.get("sections") or {}),
                source_url=str(value.get("source_url") or "https://cdrdv2.intel.com/v1/dl/getContent/671200"),
                page_start=value.get("page_start") if isinstance(value.get("page_start"), int) else None,
                page_end=value.get("page_end") if isinstance(value.get("page_end"), int) else None,
            )
            for key, value in descriptions.items()
        }
    )
    merge_pdf_enrichment(instructions, "intel-sdm", result)


__all__ = [
    "build_catalog",
    "fetch_intel_data",
    "fetch_riscv_rvv_intrinsics_data",
    "fetch_riscv_unified_db_data",
    "fetch_uops_xml",
    "link_records",
    "load_or_parse_intel_sdm",
    "_instruction_summary",
    "_normalize_operand_xtype",
    "now_iso",
    "parse_intel_sdm",
    "parse_intel_payload",
    "parse_riscv_instruction_payload",
    "parse_riscv_intrinsics_payload",
    "parse_uops_xml",
]
