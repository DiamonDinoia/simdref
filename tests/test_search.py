import unittest
from types import SimpleNamespace

from simdref.cli import _resolve_query_payload
from simdref.display import instruction_query_text, instruction_variant_items
from simdref.queries import instruction_rows_for_intrinsic
from simdref.ingest import _instruction_summary, _normalize_operand_xtype, build_catalog
from simdref.search import find_instruction, find_intrinsic, search_catalog


class SearchTests(unittest.TestCase):
    def test_intrinsic_lookup(self):
        catalog = build_catalog(offline=True)
        record = find_intrinsic(catalog, "_mm256_add_epi32")
        self.assertIsNotNone(record)
        self.assertIn("VPADDD", " ".join(record.instructions))
        self.assertIsNotNone(find_intrinsic(catalog, "_mm256_add_ps"))
        self.assertIsNotNone(find_intrinsic(catalog, "_mm256_add_pd"))

    def test_instruction_lookup(self):
        catalog = build_catalog(offline=True)
        record = find_instruction(catalog, "ADDPS")
        self.assertIsNotNone(record)
        self.assertIn("_mm_add_ps", record.linked_intrinsics)
        self.assertEqual(record.metadata["iclass"], "ADDPS")
        self.assertTrue(record.arch_details["SKL"]["measurement"])
        self.assertTrue(record.arch_details["SKL"]["doc"])

    def test_instruction_lookup_accepts_tokenized_form(self):
        catalog = build_catalog(offline=True)
        record = find_instruction(catalog, "VADDPS YMM YMM YMM")
        self.assertIsNotNone(record)
        self.assertEqual(record.key, "VADDPS (YMM, YMM, YMM)")

    def test_search(self):
        catalog = build_catalog(offline=True)
        results = search_catalog(catalog, "expandload")
        self.assertTrue(any(result.kind == "intrinsic" for result in results))

    def test_llm_exact_intrinsic_payload(self):
        catalog = build_catalog(offline=True)
        payload = _resolve_query_payload(catalog, "_mm_add_ps")
        self.assertEqual(payload["mode"], "exact")
        self.assertEqual(payload["match_kind"], "intrinsic")
        self.assertEqual(payload["intrinsic"]["name"], "_mm_add_ps")
        self.assertTrue(payload["performance"])

    def test_llm_search_payload(self):
        catalog = build_catalog(offline=True)
        payload = _resolve_query_payload(catalog, "_mm_add")
        self.assertEqual(payload["mode"], "search")
        self.assertTrue(payload["results"])

    def test_intrinsic_prefers_intrinsic_results(self):
        catalog = build_catalog(offline=True)
        results = search_catalog(catalog, "_mm_add")
        self.assertEqual(results[0].kind, "intrinsic")

    def test_mm_add_prefers_intrinsic_results(self):
        catalog = build_catalog(offline=True)
        results = search_catalog(catalog, "mm add")
        self.assertEqual(results[0].kind, "intrinsic")
        self.assertNotIn("_mm512_maskz_expandloadu_epi32", [result.title for result in results])

    def test_add_prefers_instruction_results(self):
        catalog = build_catalog(offline=True)
        results = search_catalog(catalog, "ADD")
        self.assertEqual(results[0].kind, "instruction")

    def test_mm256_add_prefers_mm256_family(self):
        catalog = build_catalog(offline=True)
        results = search_catalog(catalog, "_mm256_add")
        titles = [result.title for result in results[:3]]
        self.assertIn("_mm256_add_ps", titles)
        self.assertIn("_mm256_add_epi32", titles)
        self.assertNotIn("_mm512_maskz_expandloadu_epi32", [result.title for result in results])

    def test_intrinsic_performance_rows(self):
        catalog = build_catalog(offline=True)
        intrinsic = find_intrinsic(catalog, "_mm_add_ps")
        rows = instruction_rows_for_intrinsic(catalog, intrinsic)
        self.assertTrue(any(row["uarch"] == "SKL" for row in rows))

    def test_normalize_operand_xtype(self):
        self.assertEqual(_normalize_operand_xtype("4i8"), "i8")
        self.assertEqual(_normalize_operand_xtype("2u16"), "u16")
        self.assertEqual(_normalize_operand_xtype("int"), "i32")
        self.assertEqual(_normalize_operand_xtype("f32"), "f32")

    def test_generated_summary_for_terse_instruction(self):
        summary = _instruction_summary(
            "ADD",
            "Add",
            [
                {"type": "reg", "width": "32", "xtype": "i32", "w": "1"},
                {"type": "imm", "width": "32", "xtype": "i32", "r": "1"},
            ],
        )
        self.assertEqual(summary, "Add 32-bit integer operands.")

    def test_masked_summary_prefix(self):
        summary = _instruction_summary(
            "VADDPS",
            "Add Packed Single Precision Floating-Point Values",
            [
                {"type": "reg", "width": "128", "xtype": "f32", "w": "1"},
                {"type": "reg", "width": "64", "xtype": "i1", "r": "1"},
                {"type": "reg", "width": "128", "xtype": "f32", "r": "1"},
                {"type": "reg", "width": "128", "xtype": "f32", "r": "1"},
            ],
        )
        self.assertEqual(summary, "Masked Add Packed Single Precision Floating-Point Values.")

    def test_instruction_variants_sort_naturally_within_isa(self):
        items = [
            SimpleNamespace(mnemonic="DIV", form="DIV (M16)", isa=["I86"], key="DIV (M16)"),
            SimpleNamespace(mnemonic="DIV", form="DIV (M8)", isa=["I86"], key="DIV (M8)"),
            SimpleNamespace(mnemonic="DIV", form="DIV (R16)", isa=["I86"], key="DIV (R16)"),
            SimpleNamespace(mnemonic="DIV", form="DIV (R8l)", isa=["I86"], key="DIV (R8l)"),
        ]
        queries = [instruction_query_text(item) for item in instruction_variant_items(items)]
        self.assertLess(queries.index("DIV M8"), queries.index("DIV M16"))
        self.assertLess(queries.index("DIV R8l"), queries.index("DIV R16"))


if __name__ == "__main__":
    unittest.main()
