import unittest

from simdref.ingest_catalog import link_records
from simdref.models import InstructionRecord, IntrinsicRecord


class X86LinkingTests(unittest.TestCase):
    def test_link_records_marks_ambiguous_xed_resolution(self):
        intrinsic = IntrinsicRecord(
            name="_mm_test_ambiguous",
            signature="void _mm_test_ambiguous(void)",
            description="Synthetic ambiguous mapping.",
            header="immintrin.h",
            isa=["AVX512F"],
            instruction_refs=[{"name": "VADDPS", "form": "", "xed": "VADDPS_FAKE", "architecture": "x86"}],
        )
        instructions = [
            InstructionRecord(
                mnemonic="VADDPS",
                form="VADDPS (ZMM{k}, ZMM, ZMM)",
                summary="Masked add.",
                isa=["AVX512F"],
                metadata={"iform": "VADDPS_FAKE"},
            ),
            InstructionRecord(
                mnemonic="VADDPS",
                form="VADDPS (ZMM{k}{z}, ZMM, ZMM)",
                summary="Mask-zero add.",
                isa=["AVX512F"],
                metadata={"iform": "VADDPS_FAKE"},
            ),
        ]

        link_records([intrinsic], instructions)

        self.assertEqual(len(intrinsic.instruction_refs), 2)
        self.assertTrue(all(ref["resolution"].startswith("xed") for ref in intrinsic.instruction_refs))
        self.assertTrue(all(ref["match_count"] == "2" for ref in intrinsic.instruction_refs))

    def test_link_records_preserves_unresolved_fallback_reference(self):
        intrinsic = IntrinsicRecord(
            name="_mm_test_unresolved",
            signature="void _mm_test_unresolved(void)",
            description="Synthetic unresolved mapping.",
            header="immintrin.h",
            isa=["SSE"],
            instruction_refs=[{"name": "NOTREAL", "form": "XMM", "xed": "", "architecture": "x86"}],
        )

        link_records([intrinsic], [])

        self.assertEqual(intrinsic.instructions, ["NOTREAL (XMM)"])
        self.assertEqual(intrinsic.instruction_refs[0]["resolution"], "unresolved")
        self.assertEqual(intrinsic.instruction_refs[0]["match_count"], "0")
        self.assertNotIn("key", intrinsic.instruction_refs[0])


if __name__ == "__main__":
    unittest.main()
