"""Tests for the perf adapter (text-mode)."""

from __future__ import annotations

from pathlib import Path

from simdref.profile import get_profiler

FIXTURES = Path(__file__).parent / "fixtures"


def test_perf_adapter_parses_script_text():
    ad = get_profiler("perf")
    path = FIXTURES / "perf_script_sample.txt"
    assert ad.can_handle(path)
    samples = list(ad.ingest(path, binary=None))

    # Three cycles observations at 0x4011a4, two at 0x4011a0, one at 0x4011b0.
    cycles = [s for s in samples if s.event == "cycles"]
    by_addr = {s.address: s for s in cycles}
    assert by_addr[0x4011A4].samples == 3
    assert by_addr[0x4011A0].samples == 2
    assert by_addr[0x4011B0].samples == 1

    # Weights sum to ~1.0 per event.
    total = sum(s.weight for s in cycles)
    assert abs(total - 1.0) < 1e-9

    # Symbol captured.
    assert all(s.symbol == "saxpy" for s in cycles)

    # instructions event also captured separately.
    instrs = [s for s in samples if s.event == "instructions"]
    assert len(instrs) == 1
