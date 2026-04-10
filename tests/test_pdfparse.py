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


from simdref.pdfparse.intel import (
    parse_instruction_title,
    KNOWN_SECTIONS,
    normalize_section_name,
)


def test_parse_instruction_title_basic():
    assert parse_instruction_title("ADDPS\u2014Add Packed Single Precision Floating-Point Values") == ("ADDPS", "Add Packed Single Precision Floating-Point Values")


def test_parse_instruction_title_with_slash():
    result = parse_instruction_title("MOVDQA/VMOVDQA32/VMOVDQA64\u2014Move Aligned Packed Integer Values")
    assert result == ("MOVDQA/VMOVDQA32/VMOVDQA64", "Move Aligned Packed Integer Values")


def test_parse_instruction_title_no_emdash():
    assert parse_instruction_title("CHAPTER 3") is None


def test_parse_instruction_title_lowercase_rejected():
    assert parse_instruction_title("The Intel\u00ae Pentium\u00ae Processor (1995\u2014") is None


def test_normalize_section_name():
    assert normalize_section_name("Description") == "Description"
    assert normalize_section_name("Intel C/C++ Compiler Intrinsic Equivalent") == "Intrinsic Equivalents"
    assert normalize_section_name("Intel C/C++Compiler Intrinsic Equivalent") == "Intrinsic Equivalents"
    assert normalize_section_name("SIMD Floating-Point Exceptions") == "SIMD Floating-Point Exceptions"
    assert normalize_section_name("Numeric Exceptions") == "Numeric Exceptions"
    assert normalize_section_name("Other Exceptions") == "Other Exceptions"
    assert normalize_section_name("Flags Affected") == "Flags Affected"


def test_known_sections():
    assert "Description" in KNOWN_SECTIONS
    assert "Operation" in KNOWN_SECTIONS
    assert "Intrinsic Equivalents" in KNOWN_SECTIONS
    assert "Flags Affected" in KNOWN_SECTIONS
