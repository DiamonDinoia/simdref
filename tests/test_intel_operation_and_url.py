"""Intel ingest must capture ``<operation>`` pseudocode and synthesize URLs.

This was the gap that made ``simdref llm query _mm_permutevar_pd`` return
only a one-line summary — the SDM Operation pseudocode (which encodes the
bit-1 selector quirk) and the intrinsics-guide URL never made it into the
catalog. The LLM payload must surface both when the data is present.
"""

from __future__ import annotations

import unittest

from simdref.ingest_catalog import parse_intel_payload


_INTEL_XML_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<intrinsics_list>
  <intrinsic tech="AVX" name="_mm_permutevar_pd" rettype="__m128d">
    <CPUID>AVX</CPUID>
    <category>Swizzle</category>
    <return varname="dst" type="__m128d"/>
    <parameter varname="a" type="__m128d"/>
    <parameter varname="b" type="__m128i"/>
    <description>Shuffle double-precision (64-bit) floating-point elements in a using the control in b.</description>
    <operation>
IF (b[1] = 0)
    dst[63:0] := a[63:0]
ELSE
    dst[63:0] := a[127:64]
FI
IF (b[65] = 0)
    dst[127:64] := a[63:0]
ELSE
    dst[127:64] := a[127:64]
FI
    </operation>
    <instruction name="vpermilpd" form="xmm, xmm, xmm"/>
  </intrinsic>
  <intrinsic tech="SSE2" name="_mm_setzero_si128" rettype="__m128i">
    <CPUID>SSE2</CPUID>
    <category>Set</category>
    <return varname="dst" type="__m128i"/>
    <description>Return vector of type __m128i with all elements set to zero.</description>
    <instruction name="pxor" form="xmm, xmm"/>
  </intrinsic>
</intrinsics_list>
"""


class IntelXmlIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = {r.name: r for r in parse_intel_payload(_INTEL_XML_FIXTURE)}

    def test_operation_pseudocode_is_captured(self) -> None:
        rec = self.records["_mm_permutevar_pd"]
        op = rec.doc_sections.get("Operation", "")
        self.assertIn("b[1] = 0", op)
        self.assertIn("b[65] = 0", op)
        # Internal whitespace (newlines) preserved — pseudocode is structural.
        self.assertIn("\n", op)

    def test_url_synthesized_for_every_intrinsic(self) -> None:
        for name, rec in self.records.items():
            self.assertTrue(
                rec.url.startswith(
                    "https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html"
                ),
                f"{name} url={rec.url!r}",
            )
            self.assertIn(f"#text={name}", rec.url)

    def test_intrinsic_without_operation_has_no_section(self) -> None:
        rec = self.records["_mm_setzero_si128"]
        self.assertNotIn("Operation", rec.doc_sections)


class LLMPayloadSurfacesNewFieldsTests(unittest.TestCase):
    """Both exact-match and search-hit payloads must include url + operation."""

    def test_intrinsic_payload_includes_url_and_operation(self) -> None:
        from simdref import cli
        from simdref.models import IntrinsicRecord

        record = IntrinsicRecord(
            name="_mm_permutevar_pd",
            signature="__m128d _mm_permutevar_pd(__m128d a, __m128i b)",
            description="Shuffle elements in a using b.",
            header="immintrin.h",
            url="https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_permutevar_pd",
            doc_sections={"Operation": "IF (b[1] = 0) ..."},
        )

        # ``intrinsic_perf_summary_runtime`` only needs a connection-shaped object;
        # stub it out so we don't need a real DB.
        from unittest import mock

        with mock.patch.object(cli, "intrinsic_perf_summary_runtime", return_value=("-", "-")):
            payload = cli._llm_intrinsic_payload(conn=None, intrinsic=record)

        self.assertEqual(payload["url"], record.url)
        self.assertEqual(payload["operation"], "IF (b[1] = 0) ...")

    def test_acle_operation_is_recognised(self) -> None:
        """ARM stores its pseudocode under ``ACLE Operation``; helper must accept that key."""
        from simdref import cli
        from simdref.models import IntrinsicRecord

        record = IntrinsicRecord(
            name="vaddq_s32",
            signature="int32x4_t vaddq_s32(int32x4_t a, int32x4_t b)",
            description="Vector add.",
            header="arm_neon.h",
            doc_sections={"ACLE Operation": "for i in 0..3: r[i] = a[i] + b[i]"},
        )
        self.assertIn("a[i] + b[i]", cli._intrinsic_operation_text(record))


if __name__ == "__main__":
    unittest.main()
