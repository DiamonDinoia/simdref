"""Compressed-payload helpers (schema v13) and the DB-only catalog rebuild."""

from __future__ import annotations

from pathlib import Path

import msgpack

from conftest import build_fixture_catalog
from simdref.storage import (
    _pack_payload,
    _unpack_payload,
    build_sqlite,
    load_catalog_from_db,
)


def test_unpack_payload_reads_legacy_raw_msgpack_rows():
    """Pre-v13 databases stored raw msgpack; the reader must keep working."""
    obj = {"memory": "dst", "ops": [1, {"x": None}]}
    raw = msgpack.packb(obj, use_bin_type=True)
    assert raw[0] != 0x78  # otherwise the sniff could not exist
    assert _unpack_payload(raw) == obj


def test_pack_unpack_payload_roundtrip_is_compressed():
    obj = {"name": "vaddps", "description": {f"k{i}": "x" * 50 for i in range(20)}}
    blob = _pack_payload(obj)
    assert blob[0] == 0x78  # zlib stream marker
    assert len(blob) < len(msgpack.packb(obj, use_bin_type=True))
    assert _unpack_payload(blob) == obj


def test_load_catalog_from_db_matches_msgpack_snapshot(tmp_path: Path):
    """After the msgpack snapshot is pruned, the DB alone must rebuild the
    in-memory catalog."""
    catalog = build_fixture_catalog()
    db = tmp_path / "catalog.db"
    build_sqlite(catalog, db)

    rebuilt = load_catalog_from_db(db)
    assert len(rebuilt.intrinsics) == len(catalog.intrinsics)
    assert len(rebuilt.instructions) == len(catalog.instructions)
    # The sources table keys on `source`, so read-back order is alphabetical,
    # not ingest order — compare order-insensitively.
    assert sorted(s.source for s in rebuilt.sources) == sorted(s.source for s in catalog.sources)
    want = {i.name: i for i in catalog.intrinsics}["_mm256_add_ps"]
    got = next(i for i in rebuilt.intrinsics if i.name == "_mm256_add_ps")
    # _search_blob is a derived cache computed in __post_init__; it can be
    # stale w.r.t. post-construction linking on either side, so exclude it.
    from dataclasses import asdict

    got_d, want_d = asdict(got), asdict(want)
    got_d.pop("_search_blob", None)
    want_d.pop("_search_blob", None)
    assert got_d == want_d
