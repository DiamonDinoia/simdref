"""Regression guard for the committed RISC-V docs snapshot.

``vendor/riscv/docs_pages.json`` is the deterministic semantic source the
validator falls back to now that upstream unified-db instruction YAMLs ship
empty ``operation()`` bodies. The snapshot must stay full-content (never the
~400-byte meta-refresh stubs docs.riscv.org serves for unversioned URLs).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).parent.parent / "vendor" / "riscv" / "docs_pages.json"

REQUIRED_PAGES = {
    "https://docs.riscv.org/reference/isa/unpriv/v-st-ext.html",
    "https://docs.riscv.org/reference/isa/unpriv/vector-crypto.html",
    "https://docs.riscv.org/reference/isa/unpriv/bfloat16.html",
}


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    return json.loads(SNAPSHOT.read_text())


def test_snapshot_covers_required_pages(pages):
    assert REQUIRED_PAGES <= set(pages)
    for url in REQUIRED_PAGES:
        body = pages[url]
        assert len(body) > 4096, f"{url}: looks like a redirect stub"


def test_vector_page_yields_operations(pages):
    from simdref.riscv import _extract_instruction_section_semantics

    page = pages["https://docs.riscv.org/reference/isa/unpriv/v-st-ext.html"]
    for mnemonic in ("vsub", "vle32", "vredsum"):
        out = _extract_instruction_section_semantics(page, mnemonic)
        assert out.get("Description", "").strip(), f"{mnemonic}: no Description"
        assert out.get("Operation", "").strip(), f"{mnemonic}: no Operation"


def test_crypto_page_yields_description(pages):
    from simdref.riscv import _extract_instruction_section_semantics

    page = pages["https://docs.riscv.org/reference/isa/unpriv/vector-crypto.html"]
    out = _extract_instruction_section_semantics(page, "vaesdf")
    assert out.get("Description", "").strip()
