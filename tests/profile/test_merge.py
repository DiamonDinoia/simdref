"""Tests for the annotated ↔ samples merge."""

from __future__ import annotations

from simdref.profile.merge import merge, render_sa
from simdref.profile.model import LoopRegion, SampleRow


def _annotated():
    return [
        {
            "mnemonic": "vmovaps",
            "annotation": "lat=5c cpi=0.5",
            "address": "0x4011a0",
            "known": True,
        },
        {
            "mnemonic": "vfmadd231ps",
            "annotation": "lat=4c cpi=0.5",
            "address": "0x4011a4",
            "known": True,
        },
        {"mnemonic": "ret", "annotation": "lat=1c cpi=1.0", "address": "0x4011c1", "known": True},
    ]


def test_merge_attaches_hotness_by_address():
    samples = [
        SampleRow(address=0x4011A4, event="cycles", samples=100, weight=0.8),
        SampleRow(address=0x4011A0, event="cycles", samples=30, weight=0.2),
    ]
    merged = merge(_annotated(), samples)
    by_addr = {m.address: m for m in merged}

    assert by_addr[0x4011A4].hotness["cycles"]["weight"] == 0.8
    assert by_addr[0x4011A0].hotness["cycles"]["weight"] == 0.2
    assert by_addr[0x4011C1].hotness == {}  # no sample at ret

    # Hottest gets rank=1.
    assert by_addr[0x4011A4].hotness["rank"] == 1


def test_merge_restrict_to_marks_in_hot_loop():
    samples = [
        SampleRow(address=0x4011A4, event="cycles", samples=100, weight=0.5),
        SampleRow(address=0x4011C1, event="cycles", samples=50, weight=0.5),
    ]
    loop = LoopRegion(
        loop_id=0,
        symbol="saxpy",
        entry_address=0x4011A0,
        exit_address=0x4011B2,
        addresses=(0x4011A0, 0x4011A4, 0x4011AC),
        back_edges=((0x4011B2, 0x4011A0),),
    )
    merged = merge(_annotated(), samples, restrict_to=[loop])
    by_addr = {m.address: m for m in merged}
    assert by_addr[0x4011A4].hotness["in_hot_loop"] is True
    assert by_addr[0x4011C1].hotness["in_hot_loop"] is False


def test_render_sa_produces_bar_for_hot_lines():
    samples = [SampleRow(address=0x4011A4, event="cycles", samples=100, weight=1.0)]
    merged = merge(_annotated(), samples)
    text = render_sa(merged)
    assert "vfmadd231ps" in text
    assert "100.0%" in text
