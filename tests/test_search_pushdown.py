"""Phase D — SQL pushdown parity with in-memory filtering.

Build a tiny SQLite catalog, then verify that the set of rows returned
with ``filter_spec.sql_predicate`` pushed into SQL equals the set a
caller would produce by post-filtering in Python with
``filter_spec.matches()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simdref.filters import FilterSpec
from simdref.models import Catalog, InstructionRecord, IntrinsicRecord, SourceVersion
from simdref.storage import (
    build_sqlite,
    open_db,
    search_instruction_candidates_from_db,
    search_intrinsic_candidates_from_db,
)


def _catalog() -> Catalog:
    intrinsics = [
        IntrinsicRecord(
            name="_mm_add_ps",
            signature="__m128 _mm_add_ps(__m128 a, __m128 b)",
            description="Add packed single-precision floats.",
            header="xmmintrin.h",
            architecture="x86",
            isa=["SSE"],
            category="Arithmetic",
        ),
        IntrinsicRecord(
            name="_mm256_add_ps",
            signature="__m256 _mm256_add_ps(__m256 a, __m256 b)",
            description="Add packed single-precision floats (AVX).",
            header="immintrin.h",
            architecture="x86",
            isa=["AVX"],
            category="Arithmetic",
        ),
        IntrinsicRecord(
            name="vaddq_u8",
            signature="uint8x16_t vaddq_u8(uint8x16_t a, uint8x16_t b)",
            description="Add packed u8 lanes.",
            header="arm_neon.h",
            architecture="arm",
            isa=["NEON"],
            category="Arithmetic",
        ),
    ]
    instructions = [
        InstructionRecord(
            mnemonic="VADDPS",
            form="VADDPS xmm, xmm, xmm",
            summary="Add packed single-precision floats.",
            architecture="x86",
            isa=["AVX"],
            metadata={"category": "Arithmetic"},
        ),
        InstructionRecord(
            mnemonic="PADDD",
            form="PADDD xmm, xmm",
            summary="Add packed dword integers.",
            architecture="x86",
            isa=["SSE2"],
            metadata={"category": "Arithmetic"},
        ),
    ]
    return Catalog(
        intrinsics=intrinsics,
        instructions=instructions,
        sources=[
            SourceVersion(
                source="test",
                version="t",
                fetched_at="2025-01-01T00:00:00+00:00",
                url="test://",
            )
        ],
        generated_at="2025-01-01T00:00:00+00:00",
    )


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.db"
    build_sqlite(_catalog(), path)
    return path


def test_intrinsic_family_pushdown_matches_in_memory(db_path: Path):
    spec = FilterSpec()
    conn = open_db(db_path)
    try:
        pushed = {
            r.name
            for r in search_intrinsic_candidates_from_db(
                conn, "add", filter_spec=spec, enabled_families={"x86", "SSE", "AVX"}
            )
        }
    finally:
        conn.close()

    cat = _catalog()
    in_memory = {
        r.name
        for r in cat.intrinsics
        if spec.matches(r, enabled_families={"x86", "SSE", "AVX"})
        and "add" in r.name.casefold()
    }
    assert pushed == in_memory
    assert "vaddq_u8" not in pushed  # Arm excluded by family filter


def test_intrinsic_category_pushdown(db_path: Path):
    spec = FilterSpec()
    conn = open_db(db_path)
    try:
        pushed = {
            r.name
            for r in search_intrinsic_candidates_from_db(
                conn, "add", filter_spec=spec, enabled_categories={"Arithmetic"}
            )
        }
    finally:
        conn.close()
    assert {"_mm_add_ps", "_mm256_add_ps", "vaddq_u8"} == pushed


def test_intrinsic_category_pushdown_empty_match(db_path: Path):
    spec = FilterSpec()
    conn = open_db(db_path)
    try:
        rows = search_intrinsic_candidates_from_db(
            conn, "add", filter_spec=spec, enabled_categories={"NoSuchCategory"}
        )
    finally:
        conn.close()
    assert rows == []


def test_instruction_category_pushdown(db_path: Path):
    spec = FilterSpec()
    conn = open_db(db_path)
    try:
        rows = search_instruction_candidates_from_db(
            conn, "add", filter_spec=spec, enabled_categories={"Arithmetic"}
        )
    finally:
        conn.close()
    mnemonics = {r.mnemonic for r in rows}
    assert "VADDPS" in mnemonics
    assert "PADDD" in mnemonics


def test_instruction_family_pushdown(db_path: Path):
    spec = FilterSpec()
    conn = open_db(db_path)
    try:
        rows = search_instruction_candidates_from_db(
            conn, "add", filter_spec=spec, enabled_families={"AVX"}
        )
    finally:
        conn.close()
    mnemonics = {r.mnemonic for r in rows}
    assert "VADDPS" in mnemonics
    assert "PADDD" not in mnemonics


def test_pushdown_degrades_gracefully_without_spec(db_path: Path):
    conn = open_db(db_path)
    try:
        # No filter_spec → behave identically to the pre-Phase-D signature.
        rows = search_intrinsic_candidates_from_db(conn, "add")
    finally:
        conn.close()
    assert len(rows) == 3
