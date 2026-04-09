"""Tests for simdref.perf helpers."""

import unittest

from simdref.perf import (
    _is_numeric,
    best_cpi,
    best_latency,
    best_numeric,
    latency_cycle_values,
    variant_perf_summary,
)


class PerfTests(unittest.TestCase):
    def test_is_numeric_integers(self):
        self.assertTrue(_is_numeric("5"))
        self.assertTrue(_is_numeric("123"))

    def test_is_numeric_decimals(self):
        self.assertTrue(_is_numeric("1.5"))
        self.assertTrue(_is_numeric("0.25"))

    def test_is_numeric_rejects_non_numeric(self):
        self.assertFalse(_is_numeric(""))
        self.assertFalse(_is_numeric("abc"))
        self.assertFalse(_is_numeric("1.2.3"))
        self.assertFalse(_is_numeric("variable"))

    def test_latency_cycle_values_extracts_unique(self):
        latencies = [
            {"cycles": "3", "cycles_mem": "7"},
            {"cycles": "3", "cycles_addr": "5"},
        ]
        values = latency_cycle_values(latencies)
        self.assertEqual(values, ["3", "7", "5"])

    def test_latency_cycle_values_empty(self):
        self.assertEqual(latency_cycle_values([]), [])

    def test_best_numeric_picks_smallest(self):
        self.assertEqual(best_numeric(["5", "3", "7"]), "3")
        self.assertEqual(best_numeric(["1.5", "2.0"]), "1.5")

    def test_best_numeric_falls_back_to_first(self):
        self.assertEqual(best_numeric(["variable"]), "variable")

    def test_best_numeric_empty(self):
        self.assertEqual(best_numeric([]), "-")

    def test_best_latency(self):
        arch_details = {
            "SKL": {"latencies": [{"cycles": "4"}]},
            "HSW": {"latencies": [{"cycles": "5"}]},
        }
        self.assertEqual(best_latency(arch_details), "4")

    def test_best_latency_empty(self):
        self.assertEqual(best_latency({}), "-")

    def test_best_cpi(self):
        arch_details = {
            "SKL": {"measurement": {"TP_loop": "1.0", "TP_unrolled": "0.5"}},
        }
        self.assertEqual(best_cpi(arch_details), "0.5")

    def test_variant_perf_summary(self):
        arch_details = {
            "SKL": {
                "latencies": [{"cycles": "4"}],
                "measurement": {"TP_loop": "1.0"},
            },
        }
        lat, cpi = variant_perf_summary(arch_details)
        self.assertEqual(lat, "4")
        self.assertEqual(cpi, "1.0")


if __name__ == "__main__":
    unittest.main()
