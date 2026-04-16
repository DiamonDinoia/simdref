"""Tests for simdref.display helpers."""

import unittest
from types import SimpleNamespace

from simdref.display import (
    _CODE_SECTION_LANG,
    canonical_url,
    display_architecture,
    display_isa,
    isa_family,
    display_instruction_form,
    instruction_query_text,
    instruction_variant_items,
    isa_sort_key,
    isa_visible,
    normalize_instruction_query,
    natural_query_sort_key,
    uarch_sort_key,
)


class DisplayISATests(unittest.TestCase):
    def test_display_isa_avx512(self):
        self.assertIn("AVX512", display_isa(["AVX512F"]))

    def test_display_isa_avx10(self):
        result = display_isa(["AVX10_2"])
        self.assertIn("AVX10", result)

    def test_display_isa_amx(self):
        result = display_isa(["AMX_TILE"])
        self.assertEqual(result, "AMX-TILE")

    def test_display_isa_strips_width_suffix(self):
        result = display_isa(["AVX512F_128"])
        self.assertIn("AVX512", result)
        self.assertNotIn("128", result)

    def test_display_isa_empty(self):
        self.assertEqual(display_isa([]), "-")

    def test_display_isa_arm_tokens(self):
        self.assertEqual(display_isa(["advsimd", "SVE2p1"]), "NEON, SVE2")

    def test_display_isa_deduplicates(self):
        result = display_isa(["SSE", "SSE"])
        self.assertEqual(result, "SSE")

    def test_isa_sort_key_chronological(self):
        sse = isa_sort_key(["SSE"])
        avx = isa_sort_key(["AVX"])
        avx512 = isa_sort_key(["AVX512F"])
        self.assertLess(sse[:2], avx[:2])
        self.assertLess(avx[:2], avx512[:2])

    def test_isa_visible_hides_apx(self):
        self.assertFalse(isa_visible(["APX_F"]))

    def test_isa_visible_hides_fp16_by_default(self):
        self.assertFalse(isa_visible(["AVX512FP16"]))
        self.assertTrue(isa_visible(["AVX512FP16"], show_fp16=True))

    def test_isa_family_arm(self):
        self.assertEqual(isa_family("SVE2"), "Arm")
        self.assertEqual(isa_family("NEON"), "Arm")

    def test_display_architecture(self):
        self.assertEqual(display_architecture("arm"), "Arm")
        self.assertEqual(display_architecture("x86"), "x86")

    def test_acle_operation_uses_code_highlighting(self):
        self.assertEqual(_CODE_SECTION_LANG["ACLE Operation"], "asm")


class DisplayInstructionTests(unittest.TestCase):
    def test_instruction_query_text(self):
        item = SimpleNamespace(mnemonic="VADDPS", form="VADDPS (YMM, YMM, YMM)")
        result = instruction_query_text(item)
        self.assertEqual(result, "VADDPS YMM YMM YMM")

    def test_display_instruction_form_strips_evex(self):
        result = display_instruction_form("{evex} VADDPS (YMM, YMM, YMM)")
        self.assertNotIn("evex", result.lower())
        self.assertIn("VADDPS", result)

    def test_normalize_instruction_query(self):
        result = normalize_instruction_query("VADDPS (YMM, YMM, YMM)")
        self.assertEqual(result, "vaddps ymm ymm ymm")

    def test_natural_query_sort_key_orders_numbers(self):
        k8 = natural_query_sort_key("M8")
        k16 = natural_query_sort_key("M16")
        self.assertLess(k8, k16)

    def test_instruction_variant_items_sorted(self):
        items = [
            SimpleNamespace(mnemonic="ADD", form="ADD (R16)", isa=["I86"], key="ADD (R16)"),
            SimpleNamespace(mnemonic="ADD", form="ADD (R8l)", isa=["I86"], key="ADD (R8l)"),
        ]
        sorted_items = instruction_variant_items(items)
        keys = [item.key for item in sorted_items]
        self.assertLess(keys.index("ADD (R8l)"), keys.index("ADD (R16)"))


class DisplayMiscTests(unittest.TestCase):
    def test_canonical_url_adds_https(self):
        self.assertEqual(canonical_url("uops.info/table.html"), "https://www.uops.info/table.html")

    def test_canonical_url_preserves_https(self):
        self.assertEqual(canonical_url("https://example.com"), "https://example.com")

    def test_canonical_url_empty(self):
        self.assertEqual(canonical_url(""), "")

    def test_uarch_sort_key_known(self):
        skl = uarch_sort_key("SKL")
        hsw = uarch_sort_key("HSW")
        self.assertLess(skl, hsw)  # SKL (2015) before HSW (2013) in order list

    def test_uarch_sort_key_unknown(self):
        _, name = uarch_sort_key("UNKNOWN")
        self.assertEqual(name, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
