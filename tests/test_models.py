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
        pdf_refs=[{
            "source_id": "intel-sdm",
            "label": "Intel SDM",
            "url": "https://example.com/intel-sdm.pdf#page=42",
            "page_start": "42",
            "page_end": "43",
        }],
    )
    catalog = Catalog(intrinsics=[], instructions=[record], sources=[], generated_at="2026-01-01T00:00:00Z")
    payload = catalog.to_dict()
    roundtripped = Catalog.from_dict(payload)
    assert roundtripped.instructions[0].description == {"Description": "Adds four packed...", "Operation": "DEST[31:0] := ..."}
    assert roundtripped.instructions[0].pdf_refs[0]["source_id"] == "intel-sdm"


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


def test_instruction_record_derives_operands_and_metrics():
    record = InstructionRecord(
        mnemonic="ADDPS",
        form="ADDPS (XMM, XMM)",
        summary="Add packed single precision floating-point values.",
        operand_details=[
            {"idx": "0", "r": "1", "type": "reg", "width": "128", "xtype": "f32", "name": "xmm"},
            {"idx": "1", "w": "1", "type": "reg", "width": "128", "xtype": "f32", "name": "xmm"},
        ],
        arch_details={
            "SKL": {
                "measurement": {"TP_loop": "1.0", "uops": "1"},
                "latencies": [{"cycles": "3"}],
                "doc": {},
                "iaca": [],
            }
        },
    )
    assert record.operands == [
        "idx=0 r reg 128 f32 xmm",
        "idx=1 w reg 128 f32 xmm",
    ]
    assert record.metrics == {"SKL": {"TP_loop": "1.0", "uops": "1"}}


def test_instruction_record_normalizes_legacy_intel_metadata_to_pdf_refs():
    record = InstructionRecord(
        mnemonic="ADDPS",
        form="ADDPS (XMM, XMM)",
        summary="Add packed single precision floating-point values.",
        metadata={
            "intel-sdm-url": "https://example.com/intel-sdm.pdf#page=42",
            "intel-sdm-page-start": "42",
            "intel-sdm-page-end": "43",
        },
    )
    assert record.pdf_refs == [{
        "source_id": "intel-sdm",
        "label": "Intel SDM",
        "url": "https://example.com/intel-sdm.pdf#page=42",
        "page_start": "42",
        "page_end": "43",
    }]


def test_catalog_from_dict_with_legacy_intel_metadata_adds_pdf_refs():
    payload = {
        "intrinsics": [],
        "instructions": [{
            "mnemonic": "NOP",
            "form": "NOP",
            "summary": "No operation.",
            "isa": [],
            "operand_details": [],
            "metadata": {
                "intel-sdm-url": "https://example.com/intel-sdm.pdf#page=7",
                "intel-sdm-page-start": "7",
                "intel-sdm-page-end": "8",
            },
            "arch_details": {},
            "linked_intrinsics": [],
            "aliases": [],
            "description": {},
            "source": "uops.info",
        }],
        "sources": [],
        "generated_at": "2026-01-01T00:00:00Z",
    }
    catalog = Catalog.from_dict(payload)
    assert catalog.instructions[0].pdf_refs[0]["page_start"] == "7"
