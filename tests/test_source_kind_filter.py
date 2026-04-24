"""Tests for the --source-kind filter plumbing.

Exercises the low-level helpers rather than spinning the full CLI so tests
stay fast and deterministic.
"""

from __future__ import annotations

import unittest

from simdref.queries import filter_arch_details_by_source_kind


class SourceKindFilterTests(unittest.TestCase):
    DETAILS = {
        "skl": {"source_kind": "measured", "latencies": [{"cycles": "4"}]},
        "n1": {"source_kind": "modeled", "latencies": [{"cycles": "2"}]},
        "legacy": {"latencies": [{"cycles": "5"}]},  # no source_kind -> measured
    }

    def test_any_returns_all(self):
        self.assertEqual(
            set(filter_arch_details_by_source_kind(self.DETAILS, "any")),
            set(self.DETAILS),
        )

    def test_empty_string_returns_all(self):
        self.assertEqual(
            set(filter_arch_details_by_source_kind(self.DETAILS, "")),
            set(self.DETAILS),
        )

    def test_measured_includes_legacy(self):
        self.assertEqual(
            set(filter_arch_details_by_source_kind(self.DETAILS, "measured")),
            {"skl", "legacy"},
        )

    def test_modeled_isolates_modeled_rows(self):
        self.assertEqual(
            set(filter_arch_details_by_source_kind(self.DETAILS, "modeled")),
            {"n1"},
        )


class LlmFilterRecordsSourceKindTests(unittest.TestCase):
    def test_filters_by_measured(self):
        from simdref.cli import _llm_filter_records

        rec_measured = {
            "name": "a",
            "arch_details": {"skl": {"source_kind": "measured"}},
        }
        rec_modeled = {
            "name": "b",
            "arch_details": {"n1": {"source_kind": "modeled"}},
        }
        kept = _llm_filter_records([rec_measured, rec_modeled], None, None, source_kind="measured")
        self.assertEqual([r["name"] for r in kept], ["a"])

        kept_any = _llm_filter_records([rec_measured, rec_modeled], None, None, source_kind="any")
        self.assertEqual(len(kept_any), 2)

    def test_recurses_into_nested_results(self):
        from simdref.cli import _llm_filter_records

        rec = {
            "name": "outer",
            "instructions": [
                {"arch_details": {"n1": {"source_kind": "modeled"}}},
            ],
        }
        self.assertEqual(
            len(_llm_filter_records([rec], None, None, source_kind="modeled")),
            1,
        )
        self.assertEqual(
            len(_llm_filter_records([rec], None, None, source_kind="measured")),
            0,
        )


if __name__ == "__main__":
    unittest.main()
