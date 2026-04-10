from simdref.models import Catalog, InstructionRecord


def test_instruction_record_has_description_field():
    record = InstructionRecord(
        mnemonic="ADDPS",
        form="ADDPS (XMM, XMM)",
        summary="Add packed single precision floating-point values.",
        description={"Description": "Adds four packed...", "Operation": "DEST[31:0] := ..."},
    )
    assert record.description == {"Description": "Adds four packed...", "Operation": "DEST[31:0] := ..."}


def test_instruction_record_description_defaults_empty():
    record = InstructionRecord(mnemonic="NOP", form="NOP", summary="No operation.")
    assert record.description == {}


def test_catalog_roundtrip_with_description():
    record = InstructionRecord(
        mnemonic="ADDPS",
        form="ADDPS (XMM, XMM)",
        summary="Add packed single precision floating-point values.",
        description={"Description": "Adds four packed...", "Operation": "DEST[31:0] := ..."},
    )
    catalog = Catalog(intrinsics=[], instructions=[record], sources=[], generated_at="2026-01-01T00:00:00Z")
    payload = catalog.to_dict()
    roundtripped = Catalog.from_dict(payload)
    assert roundtripped.instructions[0].description == {"Description": "Adds four packed...", "Operation": "DEST[31:0] := ..."}


def test_catalog_from_dict_without_description():
    """Old catalogs without description field should still load."""
    payload = {
        "intrinsics": [],
        "instructions": [{
            "mnemonic": "NOP",
            "form": "NOP",
            "summary": "No operation.",
            "isa": [],
            "operands": [],
            "operand_details": [],
            "metadata": {},
            "arch_details": {},
            "linked_intrinsics": [],
            "metrics": {},
            "aliases": [],
            "source": "uops.info",
        }],
        "sources": [],
        "generated_at": "2026-01-01T00:00:00Z",
    }
    catalog = Catalog.from_dict(payload)
    assert catalog.instructions[0].description == {}
