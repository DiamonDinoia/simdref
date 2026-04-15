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
    assert any("Body." in text for _, text in sections["Description"])


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
    assert any("First body." in text for _, text in sections["Description"])
    assert "Operation" in sections
    assert any("DEST := SRC" in text for _, text in sections["Operation"])


def test_extract_sections_empty_chars():
    sections = extract_sections_from_chars([], heading_min_size=10.0, body_max_size=9.5)
    assert sections == {}


from simdref.pdfparse.intel import (
    parse_instruction_title,
    KNOWN_SECTIONS,
    normalize_section_name,
    INTEL_SDM_URL,
)
from simdref.ingest import _merge_descriptions
from simdref.models import InstructionRecord


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
    assert "FPU Flags Affected" in KNOWN_SECTIONS
    assert "Floating-Point Exceptions" in KNOWN_SECTIONS


def test_normalize_fpu_sections():
    assert normalize_section_name("FPU Flags Affected") == "FPU Flags Affected"
    assert normalize_section_name("Floating-Point Exceptions") == "Floating-Point Exceptions"


def test_whitelist_rejects_garbage_headings():
    """With known_headings, table captions at heading size are demoted to body."""
    known = frozenset({"description", "operation"})
    chars = []
    y = 100
    # "Description" at heading size — should be detected as heading
    for ch in "Description":
        chars.append(_make_char(ch, "NeoSansMedium", 10.0, top=y))
    y += 20
    for ch in "Real body text.":
        chars.append(_make_char(ch, "Verdana", 9.0, top=y))
    y += 20
    # "Table 5-8. VF..." at heading size — should NOT be a heading
    garbage = "Table 5-8. VFMSUB Notation"
    for ch in garbage:
        chars.append(_make_char(ch, "NeoSansMedium", 10.0, top=y))
    y += 20
    for ch in "More body text.":
        chars.append(_make_char(ch, "Verdana", 9.0, top=y))

    sections = extract_sections_from_chars(
        chars, heading_min_size=10.0, body_max_size=9.5, known_headings=known,
    )
    assert "Description" in sections
    # Garbage heading should NOT appear as a section
    assert "Table 5-8. VFMSUB Notation" not in sections
    # Body text after the garbage line should still be under "Description"
    body_texts = [text for _, text in sections["Description"]]
    assert "Real body text." in body_texts
    assert "More body text." in body_texts


def test_whitelist_passes_through_without_known_headings():
    """Without known_headings, font-size heuristic still works."""
    chars = []
    y = 100
    for ch in "AnyHeading":
        chars.append(_make_char(ch, "NeoSansMedium", 10.0, top=y))
    y += 20
    for ch in "Body.":
        chars.append(_make_char(ch, "Verdana", 9.0, top=y))

    sections = extract_sections_from_chars(
        chars, heading_min_size=10.0, body_max_size=9.5, known_headings=None,
    )
    assert "AnyHeading" in sections


def test_whitelist_drops_heading_size_garbage():
    """Lines at heading font size that aren't known headings are silently dropped."""
    known = frozenset({"description", "operation"})
    chars = []
    y = 100
    for ch in "Description":
        chars.append(_make_char(ch, "NeoSansMedium", 10.0, top=y))
    y += 20
    for ch in "Body line 1.":
        chars.append(_make_char(ch, "Verdana", 9.0, top=y))
    y += 20
    # Pseudocode symbol at heading size — should be dropped (not in body)
    for ch in "IF (COUNT & COUNTMASK) = 1":
        chars.append(_make_char(ch, "NeoSansMedium", 10.0, top=y))
    y += 20
    for ch in "Body line 2.":
        chars.append(_make_char(ch, "Verdana", 9.0, top=y))

    sections = extract_sections_from_chars(
        chars, heading_min_size=10.0, body_max_size=9.5, known_headings=known,
    )
    assert "Description" in sections
    body_texts = [text for _, text in sections["Description"]]
    assert "Body line 1." in body_texts
    assert "Body line 2." in body_texts
    # The garbage line at heading size should NOT appear in any section body
    all_body = " ".join(text for sec in sections.values() for _, text in sec)
    assert "IF (COUNT" not in all_body


def test_merge_descriptions_adds_sections_and_pdf_reference():
    instructions = [
        InstructionRecord(
            mnemonic="VADDPD",
            form="VADDPD (YMM, YMM, YMM)",
            summary="Add packed double-precision floating-point values.",
            isa=["AVX"],
        )
    ]
    descriptions = {
        "ADDPD": {
            "sections": {
                "Description": "Add packed double-precision floating-point values.",
                "Operation": "DEST := SRC1 + SRC2",
            },
            "page_start": 123,
            "page_end": 125,
        }
    }

    _merge_descriptions(instructions, descriptions)

    item = instructions[0]
    assert item.description["Description"].startswith("Add packed")
    assert item.metadata["intel-sdm-page-start"] == "123"
    assert item.metadata["intel-sdm-page-end"] == "125"
    assert item.metadata["intel-sdm-url"] == f"{INTEL_SDM_URL}#page=123"
