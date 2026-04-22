"""Tests for the `simdref llm` subcommand and its helpers.

Uses the shared dev-install catalog at ``data/derived/``; tests that only
exercise pure helpers (formatters, filter, schema) construct fixtures inline.
"""

import json
import unittest

from typer.testing import CliRunner

from simdref.cli import (
    LLM_EXIT_AMBIGUOUS,
    LLM_EXIT_MATCH,
    LLM_EXIT_NO_MATCH,
    LLM_EXIT_USAGE,
    _llm_exit_code,
    _llm_filter_records,
    _llm_format_markdown,
    _llm_schema_payload,
    _record_has_source_kind,
    app,
)
from simdref.storage import CATALOG_PATH, SQLITE_PATH


runner = CliRunner()


class LlmPureHelperTests(unittest.TestCase):
    def test_exit_code_exact_match_returns_zero(self):
        payload = {"query": "_mm_add_epi32", "mode": "exact", "result": {"intrinsic": "x"}}
        self.assertEqual(_llm_exit_code(payload), LLM_EXIT_MATCH)

    def test_exit_code_search_without_results_is_no_match(self):
        self.assertEqual(_llm_exit_code({"mode": "search", "results": []}), LLM_EXIT_NO_MATCH)

    def test_exit_code_multiple_exact_instructions_is_ambiguous(self):
        payload = {
            "query": "add",
            "mode": "exact",
            "match_kind": "instruction",
            "results": [{"query": "add"}, {"query": "add"}],
        }
        self.assertEqual(_llm_exit_code(payload), LLM_EXIT_AMBIGUOUS)

    def test_filter_records_by_isa_family(self):
        records = [
            {"intrinsic": "a", "isa": ["AVX512F"]},
            {"intrinsic": "b", "isa": ["NEON"]},
        ]
        kept = _llm_filter_records(records, isa=["Arm"], category=None)
        self.assertEqual([r["intrinsic"] for r in kept], ["b"])

    def test_filter_records_by_category(self):
        records = [
            {"intrinsic": "a", "isa": ["SSE"], "category": "Arithmetic"},
            {"intrinsic": "b", "isa": ["SSE"], "category": "Logical"},
        ]
        kept = _llm_filter_records(records, isa=None, category=["Logical"])
        self.assertEqual([r["intrinsic"] for r in kept], ["b"])

    def test_format_markdown_includes_intrinsic_fields(self):
        payload = {
            "query": "_mm_add_epi32",
            "mode": "exact",
            "match_kind": "intrinsic",
            "result": {
                "intrinsic": "_mm_add_epi32",
                "signature": "__m128i _mm_add_epi32(__m128i, __m128i)",
                "isa": ["SSE2"],
                "instructions": ["paddd"],
                "lat": "1",
                "cpi": "0.5",
                "summary": "Add packed 32-bit integers.",
            },
        }
        md = _llm_format_markdown(payload)
        self.assertIn("_mm_add_epi32", md)
        self.assertIn("SSE2", md)
        self.assertIn("Add packed 32-bit integers.", md)

    def test_schema_payload_declares_expected_top_level_fields(self):
        schema = _llm_schema_payload()
        self.assertIn("query", schema["properties"])
        self.assertIn("mode", schema["properties"])
        self.assertIn("results", schema["properties"])


class LlmCliIntegrationTests(unittest.TestCase):
    """End-to-end tests against the dev-install catalog."""

    @classmethod
    def setUpClass(cls):
        if not CATALOG_PATH.exists() or not SQLITE_PATH.exists():
            raise unittest.SkipTest(
                f"on-disk catalog missing (expected {CATALOG_PATH} and {SQLITE_PATH}); "
                "run `simdref build` or `simdref update --from-release` "
                "to provision it before running integration tests"
            )

    def test_llm_schema_emits_json_schema(self):
        result = runner.invoke(app, ["llm", "schema"])
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["title"], "simdref.llm")

    def test_llm_list_emits_filter_spec(self):
        result = runner.invoke(app, ["llm", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertIn("family_order", payload)
        self.assertIn("default_enabled", payload)

    def test_llm_list_markdown_format(self):
        result = runner.invoke(app, ["llm", "list", "--format", "markdown"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("ISA families", result.output)

    def test_llm_usage_error_on_unknown_format(self):
        result = runner.invoke(app, ["llm", "query", "_mm_add_epi32", "--format", "xml"])
        self.assertEqual(result.exit_code, LLM_EXIT_USAGE)

    def test_llm_exact_intrinsic_match_returns_zero(self):
        result = runner.invoke(app, ["llm", "query", "_mm_add_epi32"])
        # Exit code 0 on match, 2 if the dev catalog doesn't carry this intrinsic.
        self.assertIn(result.exit_code, (LLM_EXIT_MATCH, LLM_EXIT_NO_MATCH), result.output)
        if result.exit_code == LLM_EXIT_MATCH:
            payload = json.loads(result.output)
            self.assertEqual(payload["mode"], "exact")

    def test_llm_no_match_returns_two(self):
        result = runner.invoke(app, ["llm", "query", "nonsense_query_xyz_definitely_missing"])
        self.assertEqual(result.exit_code, LLM_EXIT_NO_MATCH, result.output)

    def test_llm_ndjson_emits_one_object_per_line(self):
        result = runner.invoke(app, ["llm", "query", "_mm_add_epi32", "--format", "ndjson"])
        if result.exit_code != LLM_EXIT_MATCH:
            self.skipTest("catalog does not carry _mm_add_epi32 in this environment")
        # Exact-match intrinsic: a single JSON line.
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        self.assertGreaterEqual(len(lines), 1)
        # Every line must be parseable JSON on its own.
        for line in lines:
            json.loads(line)

    def test_llm_invalid_preset_exits_usage(self):
        result = runner.invoke(app, ["llm", "query", "_mm_add_epi32", "--preset", "not_a_real_preset"])
        self.assertEqual(result.exit_code, LLM_EXIT_USAGE, result.output)
        self.assertIn("unknown --preset", (result.output + (result.stderr or "")))

    def test_llm_list_pattern_emits_ndjson(self):
        result = runner.invoke(app, ["llm", "list", "--pattern", "_mm_add_epi32"])
        if result.exit_code == LLM_EXIT_NO_MATCH:
            self.skipTest("catalog does not carry _mm_add_epi32 in this environment")
        self.assertEqual(result.exit_code, LLM_EXIT_MATCH, result.output)
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        self.assertGreaterEqual(len(lines), 1)
        for line in lines:
            rec = json.loads(line)
            self.assertIn(rec["kind"], ("intrinsic", "instruction"))
            self.assertIn("isa", rec)
            self.assertIn("category", rec)

    def test_llm_list_pattern_no_match_returns_two(self):
        result = runner.invoke(app, ["llm", "list", "--pattern", "__definitely_nothing_matches_this__"])
        self.assertEqual(result.exit_code, LLM_EXIT_NO_MATCH, result.output)

    def test_llm_list_pattern_with_isa_filters(self):
        result = runner.invoke(app, ["llm", "list", "--pattern", "*", "--isa", "Arm"])
        if result.exit_code == LLM_EXIT_NO_MATCH:
            self.skipTest("catalog has no Arm entries in this environment")
        self.assertEqual(result.exit_code, LLM_EXIT_MATCH, result.output)
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        self.assertGreater(len(lines), 0)

    def test_llm_batch_mixed_match_no_match(self):
        stdin = "_mm_add_epi32\n__definitely_missing__\n"
        result = runner.invoke(app, ["llm", "batch"], input=stdin)
        self.assertEqual(result.exit_code, 0, result.output)
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        records = [json.loads(l) for l in lines]
        self.assertEqual({r["query"] for r in records}, {"_mm_add_epi32", "__definitely_missing__"})
        missing_rec = next(r for r in records if r["query"] == "__definitely_missing__")
        self.assertEqual(missing_rec["status"], "no_match")

    def test_llm_batch_skips_blank_and_comment_lines(self):
        stdin = "\n# a comment\n_mm_add_epi32\n\n"
        result = runner.invoke(app, ["llm", "batch"], input=stdin)
        self.assertEqual(result.exit_code, 0, result.output)
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["query"], "_mm_add_epi32")


class LlmSchemaCompletenessTests(unittest.TestCase):
    def test_schema_declares_catalog_meta(self):
        schema = _llm_schema_payload()
        self.assertIn("generated_at", schema["properties"])
        self.assertIn("source_versions", schema["properties"])

    def test_schema_declares_instruction_refs(self):
        schema = _llm_schema_payload()
        result = schema["properties"]["result"]
        self.assertIn("instruction_refs", result["properties"])
        refs = result["properties"]["instruction_refs"]
        ref_props = refs["items"]["properties"]
        for field in ("key", "name", "form", "architecture", "xed", "resolution", "match_count"):
            self.assertIn(field, ref_props)


class LlmSourceKindFilterTests(unittest.TestCase):
    def test_record_has_measured_source(self):
        rec = {
            "arch_details": {
                "SKL": {"latency": "4", "source_kind": "measured"},
            },
        }
        self.assertTrue(_record_has_source_kind(rec, "measured"))
        self.assertFalse(_record_has_source_kind(rec, "modeled"))

    def test_record_default_source_is_measured(self):
        rec = {"arch_details": {"SKL": {"latency": "4"}}}
        self.assertTrue(_record_has_source_kind(rec, "measured"))

    def test_filter_records_by_source_kind(self):
        records = [
            {"intrinsic": "a", "arch_details": {"SKL": {"source_kind": "measured"}}},
            {"intrinsic": "b", "arch_details": {"SKL": {"source_kind": "modeled"}}},
        ]
        kept = _llm_filter_records(records, isa=None, category=None, source_kind="modeled")
        self.assertEqual([r["intrinsic"] for r in kept], ["b"])

    def test_filter_records_any_source_kind_passes_through(self):
        records = [{"intrinsic": "a", "arch_details": {"SKL": {"source_kind": "measured"}}}]
        kept = _llm_filter_records(records, isa=None, category=None, source_kind="any")
        self.assertEqual(kept, records)


if __name__ == "__main__":
    unittest.main()
