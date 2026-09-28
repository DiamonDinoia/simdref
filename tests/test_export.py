"""Tests for simdref.export: the JSON site-data contract with the web repo."""

from __future__ import annotations

import json
from pathlib import Path

from simdref.export import export_site_data
from simdref.models import Catalog, InstructionRecord, SourceVersion


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
