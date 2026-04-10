import pytest
from simdref.pdfparse.base import extract_sections_from_chars


def _make_char(text, fontname, size, x0=0, top=0):
    """Create a minimal char dict matching pdfplumber's char format."""
    return {"text": text, "fontname": fontname, "size": size, "x0": x0, "top": top}


def test_extract_sections_basic():
    """Section headers at size 10, body text at size 9."""
    chars = [
        _make_char("D", "NeoSansMedium", 10.0, x0=0, top=100),
        _make_char("e", "NeoSansMedium", 10.0, x0=8, top=100),
        _make_char("s", "NeoSansMedium", 10.0, x0=16, top=100),
        _make_char("c", "NeoSansMedium", 10.0, x0=24, top=100),
        _make_char("r", "NeoSansMedium", 10.0, x0=32, top=100),
        _make_char("i", "NeoSansMedium", 10.0, x0=40, top=100),
        _make_char("p", "NeoSansMedium", 10.0, x0=48, top=100),
        _make_char("t", "NeoSansMedium", 10.0, x0=56, top=100),
        _make_char("i", "NeoSansMedium", 10.0, x0=64, top=100),
        _make_char("o", "NeoSansMedium", 10.0, x0=72, top=100),
        _make_char("n", "NeoSansMedium", 10.0, x0=80, top=100),
        _make_char("B", "Verdana", 9.0, x0=0, top=120),
        _make_char("o", "Verdana", 9.0, x0=8, top=120),
        _make_char("d", "Verdana", 9.0, x0=16, top=120),
        _make_char("y", "Verdana", 9.0, x0=24, top=120),
        _make_char(".", "Verdana", 9.0, x0=32, top=120),
    ]
    sections = extract_sections_from_chars(chars, heading_min_size=10.0, body_max_size=9.5)
    assert "Description" in sections
    assert "Body." in sections["Description"]


def test_extract_sections_multiple():
    """Multiple sections detected in sequence."""
    chars = []
    y = 100
    for ch in "Description":
        chars.append(_make_char(ch, "NeoSansMedium", 10.0, top=y))
    y += 20
    for ch in "First body.":
        chars.append(_make_char(ch, "Verdana", 9.0, top=y))
    y += 40
    for ch in "Operation":
        chars.append(_make_char(ch, "NeoSansMedium", 10.0, top=y))
    y += 20
    for ch in "DEST := SRC":
        chars.append(_make_char(ch, "Verdana", 9.0, top=y))

    sections = extract_sections_from_chars(chars, heading_min_size=10.0, body_max_size=9.5)
    assert "Description" in sections
    assert "First body." in sections["Description"]
    assert "Operation" in sections
    assert "DEST := SRC" in sections["Operation"]


def test_extract_sections_empty_chars():
    sections = extract_sections_from_chars([], heading_min_size=10.0, body_max_size=9.5)
    assert sections == {}
