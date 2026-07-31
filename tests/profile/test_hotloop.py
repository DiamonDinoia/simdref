"""Tests for hot-loop detection."""

from __future__ import annotations

from pathlib import Path

from simdref.profile.hotloop import detect_loops, parse_objdump, rank_loops
from simdref.profile.model import SampleRow

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_objdump_extracts_addresses_and_branches():
    text = (FIXTURES / "saxpy.objdump").read_text()
    instrs = parse_objdump(text)

    addrs = {i.address for i in instrs}
    assert 0x4011A0 in addrs
    assert 0x4011B2 in addrs

    # The `jl 4011a0` at 0x4011b2 is the back edge into the loop head.
    backedge = next(i for i in instrs if i.address == 0x4011B2)
    assert backedge.target == 0x4011A0
    assert backedge.mnemonic == "jl"


def test_detect_loops_finds_backedge():
    instrs = parse_objdump((FIXTURES / "saxpy.objdump").read_text())
    loops = detect_loops(instrs)
    assert len(loops) == 1
    loop = loops[0]
    assert loop.symbol == "saxpy"
    assert loop.entry_address == 0x4011A0
    assert loop.exit_address == 0x4011B2
    # All loop-body addresses between entry and exit inclusive.
    assert 0x4011A4 in loop.addresses
    assert 0x4011AC in loop.addresses


def test_rank_loops_uses_cycles_weight():
    instrs = parse_objdump((FIXTURES / "saxpy.objdump").read_text())
    loops = detect_loops(instrs)
    samples = [
        SampleRow(address=0x4011A4, event="cycles", samples=100, weight=0.7),
        SampleRow(address=0x4011A0, event="cycles", samples=30, weight=0.2),
        SampleRow(address=0x4011C0, event="cycles", samples=10, weight=0.1),  # outside loop
    ]
    ranked = rank_loops(loops, samples, event="cycles")
    assert ranked[0].total_weight == 0.7 + 0.2
    assert ranked[0].loop_id == loops[0].loop_id


def test_filter_disasm_keeps_only_hot_blocks():
    from simdref.profile.hotloop import filter_disasm

    text = (FIXTURES / "saxpy.objdump").read_text()
    keep = {0x4011A4, 0x4011B2}
    out = filter_disasm(text, keep)
    kept_lines = [l for l in out.splitlines() if l.strip()]

    # Both kept instructions survive with their addresses.
    assert any("4011a4" in l for l in kept_lines)
    assert any("4011b2" in l for l in kept_lines)
    # Cold instructions are gone (match on the line prefix, not substrings:
    # the kept `jl` target text legitimately mentions 4011a0).
    assert not any(l.startswith("  4011a0:") for l in kept_lines)
    # Symbol header is preserved so annotate threads the symbol name.
    assert any("<saxpy>" in l for l in kept_lines)
