"""PDF source registry."""

from __future__ import annotations

from simdref.pdfparse.types import PdfSourceSpec

_PDF_SOURCES: dict[str, PdfSourceSpec] = {}


def register_pdf_source(spec: PdfSourceSpec) -> None:
    _PDF_SOURCES[spec.source_id] = spec


def get_pdf_source(source_id: str) -> PdfSourceSpec:
    return _PDF_SOURCES[source_id]


def iter_pdf_sources() -> tuple[PdfSourceSpec, ...]:
    return tuple(_PDF_SOURCES.values())
