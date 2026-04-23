"""Back-compat + position tracking in `simdref annotate`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from simdref.annotate import AnnotateOptions, annotate_stream, parse_asm_line


def test_objdump_line_address_parsed_with_track_positions():
    line = "  4011a4:\tvfmadd231ps %xmm2,%xmm1,%xmm0"
    parsed = parse_asm_line(line, track_positions=True)
    assert parsed.address == 0x4011A4
    assert parsed.mnemonic == "vfmadd231ps"


def test_non_tracked_mode_unchanged():
    # Without track_positions, `4011a4:` would be interpreted as a label.
    line = "  4011a4:\tvfmadd231ps %xmm2,%xmm1,%xmm0"
    parsed = parse_asm_line(line)
    # Should still recognize the instruction part; either way no address.
    assert parsed.address is None


def test_annotate_json_with_track_positions_emits_address(monkeypatch):
    # Stub the catalog lookup so we don't depend on the live DB.
    from simdref import annotate as _annotate

    class _FakeRecord:
        summary = "Fused multiply-add of packed single-precision floats."
        arch_details = {"skylake-x": {"latencies": [{"cycles": 4}], "throughput": 0.5, "source_kind": "measured"}}
        operand_details = []
        key = "VFMADD231PS (XMM, XMM, XMM)"

    monkeypatch.setattr(_annotate, "lookup", lambda mn, conn: [_FakeRecord()])
    monkeypatch.setattr(
        _annotate,
        "pick_record",
        lambda records, *, arch=None, operands="": records[0] if records else None,
    )

    conn = sqlite3.connect(":memory:")
    line = "  4011a4:\tvfmadd231ps %xmm2,%xmm1,%xmm0\n"
    opts = AnnotateOptions(fmt="json", track_positions=True)
    out = "".join(annotate_stream(iter([line]), opts=opts, conn=conn))
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["address"] == "0x4011a4"
