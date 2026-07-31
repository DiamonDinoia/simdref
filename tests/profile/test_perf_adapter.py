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


def test_parse_script_lines_handles_demangled_symbols():
    """Demangled C++ symbols contain spaces; the regex must span them (#22)."""
    from simdref.profile.adapters.perf import _parse_script_lines

    sym = (
        "(anonymous namespace)::make_kernel<1, true>()::{lambda(int, int)#2}"
        "::__invoke(int, int) const [clone .llvm.1]"
    )
    text = f"  8157 cycles:  40114a {sym}+0x4 (drv)\n"
    rows = list(_parse_script_lines(text))
    assert len(rows) == 1
    ip, event, period, got_sym, symoff, dso = rows[0]
    assert ip == 0x40114A
    assert event == "cycles"
    assert period == 8157
    assert got_sym == sym
    assert symoff == 4
    assert dso == "drv"


def test_resolve_va_disambiguates_duplicate_symbols():
    from simdref.profile.adapters.perf import _resolve_va

    # 4 identical thin-LTO clones: sized copies containing the offset
    sized = [(0x4290, 0x80), (0x1250, 0x80), (0x2F90, 0x80), (0x20F0, 0x80)]
    assert _resolve_va(sized, 0x10) == 0x1250  # lowest containing VA
    assert _resolve_va(sized, 0x100) == 0x1250  # no size contains -> lowest VA
    assert _resolve_va([(0x1000, None)], 0x10) == 0x1000
    assert _resolve_va([], 0x10) is None


def test_run_perf_script_disables_demangling(tmp_path, monkeypatch):
    """perf script must run with --no-demangle so names match `nm -S`."""
    from simdref.profile.adapters import perf as perf_mod

    captured: dict[str, list[str]] = {}

    class _Res:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Res()

    monkeypatch.setattr(perf_mod.shutil, "which", lambda name: "/usr/bin/perf")
    monkeypatch.setattr(perf_mod.subprocess, "run", fake_run)
    perf_mod._run_perf_script(tmp_path / "perf.data")
    assert "--no-demangle" in captured["cmd"]
