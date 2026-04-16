"""Vendor-neutral PDF enrichment types."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PdfDescriptionPayload:
    sections: dict[str, str]
    source_url: str
    page_start: int | None = None
    page_end: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "sections": self.sections,
            "source_url": self.source_url,
            "page_start": self.page_start,
            "page_end": self.page_end,
        }


@dataclass(frozen=True, slots=True)
class PdfEnrichmentResult:
    descriptions: dict[str, PdfDescriptionPayload]
    fallback_page_count: int = 0
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "descriptions": {key: value.to_dict() for key, value in self.descriptions.items()},
            "fallback_page_count": self.fallback_page_count,
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "PdfEnrichmentResult":
        descriptions: dict[str, PdfDescriptionPayload] = {}
        raw_descriptions = payload.get("descriptions") or {}
        if isinstance(raw_descriptions, dict):
            for key, value in raw_descriptions.items():
                if not isinstance(value, dict):
                    continue
                descriptions[str(key)] = PdfDescriptionPayload(
                    sections=dict(value.get("sections") or {}),
                    source_url=str(value.get("source_url") or ""),
                    page_start=value.get("page_start") if isinstance(value.get("page_start"), int) else None,
                    page_end=value.get("page_end") if isinstance(value.get("page_end"), int) else None,
                )
        raw_stats = payload.get("stats") or {}
        stats = {
            str(key): int(value)
            for key, value in raw_stats.items()
            if isinstance(value, int)
        } if isinstance(raw_stats, dict) else {}
        fallback_page_count = payload.get("fallback_page_count")
        return cls(
            descriptions=descriptions,
            fallback_page_count=fallback_page_count if isinstance(fallback_page_count, int) else 0,
            stats=stats,
        )


@dataclass(frozen=True, slots=True)
class PdfSourceSpec:
    source_id: str
    display_name: str
    source_url: str
    local_candidates: Sequence[Path]
    cache_path: Path
    cache_version: int
    signature_paths: Sequence[Path]
    parser: Callable[..., PdfEnrichmentResult]
    find_source: Callable[..., Path | None]
