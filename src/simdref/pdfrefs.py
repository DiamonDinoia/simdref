"""Shared normalized PDF reference helpers."""

from __future__ import annotations

from typing import Any

PdfRef = dict[str, str]

_LEGACY_INTEL_SOURCE_ID = "intel-sdm"
_LEGACY_INTEL_LABEL = "Intel SDM"


def normalize_pdf_refs(
    pdf_refs: list[dict[str, Any]] | None,
    metadata: dict[str, str] | None = None,
) -> list[PdfRef]:
    """Return normalized PDF references, preserving legacy Intel metadata."""
    normalized: list[PdfRef] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for ref in pdf_refs or []:
        candidate = {
            "source_id": str(ref.get("source_id") or "").strip(),
            "label": str(ref.get("label") or "").strip(),
            "url": str(ref.get("url") or "").strip(),
            "page_start": str(ref.get("page_start") or "").strip(),
            "page_end": str(ref.get("page_end") or "").strip(),
        }
        if not candidate["source_id"] or not candidate["label"] or not candidate["url"]:
            continue
        key = (
            candidate["source_id"],
            candidate["label"],
            candidate["url"],
            candidate["page_start"],
            candidate["page_end"],
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)

    legacy = legacy_intel_pdf_ref(metadata)
    if legacy is not None:
        key = (
            legacy["source_id"],
            legacy["label"],
            legacy["url"],
            legacy["page_start"],
            legacy["page_end"],
        )
        if key not in seen:
            normalized.append(legacy)
    return normalized


def legacy_intel_pdf_ref(metadata: dict[str, str] | None) -> PdfRef | None:
    """Build a normalized ref from legacy Intel-specific metadata keys."""
    if not metadata:
        return None
    url = (metadata.get("intel-sdm-url") or "").strip()
    if not url:
        return None
    return {
        "source_id": _LEGACY_INTEL_SOURCE_ID,
        "label": _LEGACY_INTEL_LABEL,
        "url": url,
        "page_start": (metadata.get("intel-sdm-page-start") or "").strip(),
        "page_end": (metadata.get("intel-sdm-page-end") or "").strip(),
    }


def apply_legacy_pdf_metadata(metadata: dict[str, str], pdf_refs: list[PdfRef]) -> dict[str, str]:
    """Populate legacy Intel keys for backward-compatible payloads."""
    legacy = next((ref for ref in pdf_refs if ref.get("source_id") == _LEGACY_INTEL_SOURCE_ID), None)
    if legacy is None:
        return metadata
    if legacy.get("url"):
        metadata.setdefault("intel-sdm-url", legacy["url"])
    if legacy.get("page_start"):
        metadata.setdefault("intel-sdm-page-start", legacy["page_start"])
    if legacy.get("page_end"):
        metadata.setdefault("intel-sdm-page-end", legacy["page_end"])
    return metadata


def pdf_ref_label(ref: PdfRef) -> str:
    label = ref.get("label") or ref.get("source_id") or "PDF"
    page_start = ref.get("page_start") or ""
    page_end = ref.get("page_end") or ""
    if page_start and page_end and page_end != page_start:
        return f"{label} (pages {page_start}-{page_end})"
    if page_start:
        return f"{label} (page {page_start})"
    return label
