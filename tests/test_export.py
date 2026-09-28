"""Tests for simdref.export: schema_version and latency-index.json parity.

``test_latency_index_matches_catalog_db`` and its positive control load the
real local catalog (see ``conftest.load_any_catalog``) because the row
requires 200 sampled entries; the bundled test fixtures only carry 32
instructions.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from simdref.export import export_site_data
from simdref.models import Catalog, InstructionRecord, SourceVersion
from simdref.perf import variant_perf_summary
from simdref.storage import build_sqlite, load_instruction_from_db, open_db


def _tiny_catalog() -> Catalog:
    return Catalog(
        intrinsics=[],
        instructions=[
            InstructionRecord(
                mnemonic="VADDPS",
                form="xmm, xmm, xmm",
                architecture="x86",
                summary="Add packed single-precision floats.",
                isa=["AVX"],
            )
        ],
        sources=[
            SourceVersion(
                source="test", version="t", fetched_at="2025-01-01T00:00:00+00:00", url="test://"
            )
        ],
        generated_at="2025-01-01T00:00:00+00:00",
    )


def test_schema_version_is_int(tmp_path: Path):
    export_site_data(_tiny_catalog(), tmp_path)
    stamp = json.loads((tmp_path / "build_stamp.json").read_text())
    assert isinstance(stamp["schema_version"], int)


@pytest.fixture(scope="module")
def latency_index_and_db(tmp_path_factory) -> tuple[dict, Path]:
    """Real-catalog latency-index.json plus the catalog.db exported alongside it.

    Module-scoped: both consuming tests query the same build instead of
    rebuilding the ~29k-instruction sqlite file twice.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from conftest import load_any_catalog

        catalog = load_any_catalog()
    finally:
        sys.path.pop(0)

    tmp_path = tmp_path_factory.mktemp("export")
    build_sqlite(catalog, tmp_path / "catalog.db")
    export_site_data(catalog, tmp_path / "web")
    latency_index = json.loads((tmp_path / "web" / "latency-index.json").read_text())
    assert len(latency_index) >= 200, "catalog too small for a 200-entry sample"
    return latency_index, tmp_path / "catalog.db"


def test_latency_index_matches_catalog_db(latency_index_and_db):
    """200 sampled latency-index.json entries match a direct catalog.db query."""
    latency_index, db_path = latency_index_and_db
    conn = open_db(db_path)
    try:
        sample = random.Random(0).sample(list(latency_index), 200)
        for key in sample:
            record = load_instruction_from_db(conn, key)
            assert record is not None, f"{key!r} missing from catalog.db"
            lat, cpi = variant_perf_summary(record.arch_details)
            assert latency_index[key] == {"lat": lat, "cpi": cpi}, key
    finally:
        conn.close()


def test_latency_index_corruption_is_caught(latency_index_and_db):
    """Positive control: a corrupted entry must fail the same comparison."""
    latency_index, db_path = latency_index_and_db
    conn = open_db(db_path)
    try:
        key = next(iter(latency_index))
        record = load_instruction_from_db(conn, key)
        lat, cpi = variant_perf_summary(record.arch_details)
        corrupted = dict(latency_index[key])
        corrupted["lat"] = corrupted["lat"] + "-corrupted"
        assert corrupted != {"lat": lat, "cpi": cpi}
    finally:
        conn.close()
