"""Attach ingested perf rows onto existing :class:`InstructionRecord` data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from simdref.models import InstructionRecord


@dataclass(frozen=True)
class PerfRow:
    """A single perf observation produced by a perf_sources ingester.

    Attributes map onto the provenance keys stored in
    ``InstructionRecord.arch_details[core]`` so :func:`merge_perf_rows` can
    drop them in verbatim.
    """

    mnemonic: str
    core: str
    source: str
    source_kind: str  # "measured" | "modeled"
    source_version: str
    architecture: str = ""
    form: str = ""
    latency: str = ""
    cpi: str = ""
    applies_to: str = "mnemonic"
    citation_url: str = ""
    extra_measurement: dict[str, str] = field(default_factory=dict)

    def as_arch_details_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "source": self.source,
            "source_kind": self.source_kind,
            "source_version": self.source_version,
            "applies_to": self.applies_to,
            "citation_url": self.citation_url,
        }
        if self.latency:
            entry["latencies"] = [{"cycles": self.latency}]
        measurement: dict[str, str] = dict(self.extra_measurement)
        if self.cpi:
            measurement.setdefault("TP", self.cpi)
        if measurement:
            entry["measurement"] = measurement
        return entry


def _record_key(record: InstructionRecord) -> tuple[str, str, str]:
    return (
        record.architecture.casefold(),
        record.mnemonic.casefold(),
        record.form.strip().casefold(),
    )


def merge_perf_rows(
    records: Iterable[InstructionRecord],
    rows: Iterable[PerfRow],
    *,
    overwrite: bool = False,
) -> int:
    """Merge *rows* into matching records in-place.

    Matching rule: a row attaches to every record with the same architecture
    and mnemonic. If ``row.form`` is non-empty, only records with the
    matching form are updated (case-insensitive). Otherwise the row applies
    to every variant of the mnemonic (``applies_to="mnemonic"``).

    ``overwrite=False`` (default) preserves existing ``arch_details[core]``
    entries so measured data produced by earlier passes is never clobbered
    by modeled data from a later pass.

    Returns the number of ``arch_details[core]`` entries newly written.
    """
    records = list(records)
    by_arch_mnemonic: dict[tuple[str, str], list[InstructionRecord]] = {}
    for record in records:
        by_arch_mnemonic.setdefault(
            (record.architecture.casefold(), record.mnemonic.casefold()),
            [],
        ).append(record)

    written = 0
    for row in rows:
        targets = by_arch_mnemonic.get(
            (row.architecture.casefold() or _arch_guess(row.core), row.mnemonic.casefold()),
            [],
        )
        if not targets:
            continue
        form_key = row.form.strip().casefold()
        matched = [r for r in targets if not form_key or r.form.strip().casefold() == form_key]
        if not matched and form_key:
            matched = targets  # graceful fallback: attach to every variant
        entry = row.as_arch_details_entry()
        for record in matched:
            if not overwrite and row.core in record.arch_details:
                continue
            record.arch_details[row.core] = entry
            written += 1
    return written


def _arch_guess(core: str) -> str:
    """Best-effort architecture lookup used when :class:`PerfRow` lacks one."""
    from simdref.perf_sources.cores import core_architecture
    arch = core_architecture(core)
    if arch == "aarch64":
        return "arm"
    if arch == "riscv":
        return "riscv"
    return arch or ""
