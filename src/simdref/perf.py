"""Shared performance-metric extraction helpers.

Functions in this module extract latency, throughput (cycles-per-instruction),
and other microarchitecture-level performance data from the nested
``arch_details`` dictionaries stored on :class:`~simdref.models.InstructionRecord`.

They are consumed by the CLI, LSP, web-export, and man-page modules so
that the extraction logic lives in exactly one place.
"""

from __future__ import annotations

from typing import Any


def _is_numeric(value: str) -> bool:
    """Check if a string is a non-negative number (integer or decimal)."""
    if not value:
        return False
    parts = value.split(".", 1)
    return all(p.isdigit() for p in parts) and parts[0] != ""


def latency_cycle_values(latencies: list[dict[str, Any]]) -> list[str]:
    """Collect unique cycle-count strings from a list of latency dicts.

    Each *latency* dict may contain keys like ``cycles``, ``cycles_mem``,
    ``cycles_addr``, or ``cycles_addr_index``.  Values are collected in
    order, skipping duplicates.

    Returns:
        De-duplicated list of cycle-count strings (may be empty).
    """
    values: list[str] = []
    for latency in latencies:
        for key in ("cycles", "cycles_mem", "cycles_addr", "cycles_addr_index"):
            value = latency.get(key)
            if value and value not in values:
                values.append(value)
    return values


def best_numeric(values: list[str]) -> str:
    """Return the smallest numeric string from *values*, or ``"-"`` if empty.

    Non-numeric entries (e.g. ``"variable"``) are ignored when a numeric
    alternative exists.  If every entry is non-numeric the first value is
    returned as-is.
    """
    if not values:
        return "-"
    numeric = [v for v in values if _is_numeric(str(v))]
    if numeric:
        return min(numeric, key=lambda v: float(v))
    return values[0]


def best_latency(arch_details: dict[str, dict[str, Any]]) -> str:
    """Return the best (lowest) latency across all microarchitectures.

    Iterates every architecture entry in *arch_details*, collects all
    latency cycle values, and returns the smallest numeric one.
    """
    values: list[str] = []
    for details in arch_details.values():
        values.extend(latency_cycle_values(details.get("latencies") or []))
    return best_numeric(values)


def best_cpi(arch_details: dict[str, dict[str, Any]]) -> str:
    """Return the best (lowest) cycles-per-instruction across all microarchitectures.

    Looks at throughput fields (``TP_unrolled``, ``TP_loop``, ``TP_ports``,
    ``TP``) inside each architecture's ``measurement`` block.
    """
    values: list[str] = []
    for details in arch_details.values():
        measurement = details.get("measurement") or {}
        for key in ("TP_unrolled", "TP_loop", "TP_ports", "TP"):
            value = measurement.get(key)
            if value and value not in values:
                values.append(value)
    return best_numeric(values)


def variant_perf_summary(arch_details: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """Return ``(best_latency, best_cpi)`` for a single instruction variant.

    This is a convenience wrapper combining :func:`best_latency` and
    :func:`best_cpi` into a single call, returning a ``(latency, cpi)`` pair.
    """
    return best_latency(arch_details), best_cpi(arch_details)
