"""Tests for the new perf_sources ingesters + canonical core table.

All network interactions are stubbed via injected ``fetch`` callables so
the tests stay deterministic and run offline.
"""

from __future__ import annotations

import unittest

from simdref.models import InstructionRecord
from simdref.perf_sources import (
    CANONICAL_CORES,
    PerfRow,
    canonical_core_id,
    core_architecture,
    ingest_osaca,
    ingest_rvv_bench,
    merge_perf_rows,
    parse_llvm_mca_json,
    parse_osaca_yaml,
    parse_rvv_bench_json,
)
from simdref.perf_sources.cores import CoreSpec


class CoresTests(unittest.TestCase):
    def test_canonical_roundtrip_for_every_alias(self):
        for core in CANONICAL_CORES:
            for alias in core.aliases:
                self.assertEqual(canonical_core_id(alias), core.canonical_id)

    def test_unknown_returns_none(self):
        self.assertIsNone(canonical_core_id("no-such-cpu"))
        self.assertIsNone(canonical_core_id(""))

    def test_core_architecture(self):
        self.assertEqual(core_architecture("neoverse-n1"), "aarch64")
        self.assertEqual(core_architecture("c908"), "riscv")
        self.assertIsNone(core_architecture("nope"))

    def test_aarch64_and_riscv_cores_disjoint(self):
        arches = {c.architecture for c in CANONICAL_CORES}
        self.assertEqual(arches, {"aarch64", "riscv"})


class MergeTests(unittest.TestCase):
    def _record(self, arch: str, mnemonic: str, form: str = "") -> InstructionRecord:
        return InstructionRecord(
            mnemonic=mnemonic,
            form=form,
            summary="",
            architecture=arch,
            isa=[],
        )

    def test_attaches_row_to_matching_record(self):
        record = self._record("arm", "FMLA")
        row = PerfRow(
            mnemonic="FMLA",
            core="neoverse-n1",
            source="llvm-mca",
            source_kind="modeled",
            source_version="18.1.0",
            architecture="arm",
            latency="4",
            cpi="0.5",
            citation_url="https://example/cite",
        )
        written = merge_perf_rows([record], [row])
        self.assertEqual(written, 1)
        entry = record.arch_details["neoverse-n1"]
        self.assertEqual(entry["source"], "llvm-mca")
        self.assertEqual(entry["source_kind"], "modeled")
        self.assertEqual(entry["latencies"], [{"cycles": "4"}])
        self.assertEqual(entry["measurement"], {"TP": "0.5"})
        self.assertEqual(entry["citation_url"], "https://example/cite")

    def test_does_not_overwrite_existing_by_default(self):
        record = self._record("arm", "FMLA")
        record.arch_details["neoverse-n1"] = {
            "source_kind": "measured", "latencies": [{"cycles": "3"}],
        }
        row = PerfRow(
            mnemonic="FMLA", core="neoverse-n1", source="llvm-mca",
            source_kind="modeled", source_version="18.1.0",
            architecture="arm", latency="99",
        )
        written = merge_perf_rows([record], [row])
        self.assertEqual(written, 0)
        self.assertEqual(record.arch_details["neoverse-n1"]["source_kind"], "measured")

    def test_overwrite_flag_replaces(self):
        record = self._record("arm", "FMLA")
        record.arch_details["neoverse-n1"] = {"source_kind": "modeled"}
        row = PerfRow(
            mnemonic="FMLA", core="neoverse-n1", source="osaca",
            source_kind="measured", source_version="osaca@abc",
            architecture="arm", latency="3",
        )
        merge_perf_rows([record], [row], overwrite=True)
        self.assertEqual(record.arch_details["neoverse-n1"]["source_kind"], "measured")

    def test_form_match_narrows_to_one_variant(self):
        a = self._record("arm", "FMLA", form="FMLA V0.4S, V1.4S, V2.4S")
        b = self._record("arm", "FMLA", form="FMLA V0.2D, V1.2D, V2.2D")
        row = PerfRow(
            mnemonic="FMLA", core="neoverse-n1", source="osaca",
            source_kind="measured", source_version="osaca@abc",
            architecture="arm",
            form="FMLA V0.4S, V1.4S, V2.4S",
            latency="4",
        )
        merge_perf_rows([a, b], [row])
        self.assertIn("neoverse-n1", a.arch_details)
        self.assertNotIn("neoverse-n1", b.arch_details)

    def test_missing_mnemonic_is_noop(self):
        record = self._record("arm", "FMLA")
        row = PerfRow(
            mnemonic="UNKNOWN", core="neoverse-n1", source="x",
            source_kind="measured", source_version="v", architecture="arm",
        )
        written = merge_perf_rows([record], [row])
        self.assertEqual(written, 0)


class LLVMMcaParseTests(unittest.TestCase):
    def _core(self) -> CoreSpec:
        return next(c for c in CANONICAL_CORES if c.canonical_id == "neoverse-n1")

    def test_parses_latency_and_ipc(self):
        payload = {
            "CodeRegions": [
                {
                    "Instructions": [{"Latency": 4, "Opcode": "fmla"}],
                    "SummaryView": {"IPC": 2.0},
                }
            ]
        }
        row = parse_llvm_mca_json(
            payload, core=self._core(), mnemonic="FMLA", mca_version="18.1.0"
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.latency, "4")
        self.assertEqual(row.cpi, "0.5")
        self.assertEqual(row.source_kind, "modeled")
        self.assertEqual(row.core, "neoverse-n1")

    def test_missing_regions_returns_none(self):
        self.assertIsNone(
            parse_llvm_mca_json({}, core=self._core(), mnemonic="X", mca_version="18.0")
        )
        self.assertIsNone(
            parse_llvm_mca_json(
                {"CodeRegions": []}, core=self._core(), mnemonic="X", mca_version="18.0"
            )
        )


class OSACAParseTests(unittest.TestCase):
    SAMPLE = """\
- name: FMLA
  latency: 4
  throughput: 0.5
  operands: V0.4S, V1.4S, V2.4S
- name: FADD
  latency: 3
  throughput: 1
"""

    def test_parse_yaml_subset(self):
        entries = parse_osaca_yaml(self.SAMPLE)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].mnemonic, "FMLA")
        self.assertEqual(entries[0].latency, "4")
        self.assertEqual(entries[0].cpi, "0.5")
        self.assertEqual(entries[0].operands, "V0.4S, V1.4S, V2.4S")
        self.assertEqual(entries[1].mnemonic, "FADD")
        self.assertEqual(entries[1].operands, "")

    def test_ingest_uses_injected_fetch(self):
        captured: list[str] = []

        def fake_fetch(url: str) -> str:
            captured.append(url)
            return self.SAMPLE

        rows = ingest_osaca(fetch=fake_fetch)
        self.assertTrue(captured)
        self.assertTrue(rows)
        self.assertTrue(all(r.source_kind == "measured" for r in rows))
        self.assertTrue(all(r.source == "osaca" for r in rows))

    def test_ingest_fetch_failure_yields_no_rows(self):
        def broken(url: str) -> str:
            raise RuntimeError("network down")

        rows = ingest_osaca(fetch=broken)
        self.assertEqual(rows, [])


class RVVBenchParseTests(unittest.TestCase):
    PAYLOAD = {
        "cores": {
            "c908": {
                "vfadd.vv": {"m1": 4.0, "m2": 8.0},
                "vmul.vv": {"m1": 5},
            },
            "unknown-cpu": {"vfadd.vv": {"m1": 99}},
        }
    }

    def test_parse_produces_measured_rows(self):
        import json as _json

        rows = parse_rvv_bench_json(_json.dumps(self.PAYLOAD))
        canonical_c908 = canonical_core_id("c908")
        self.assertIsNotNone(canonical_c908)
        cores = {r.core for r in rows}
        self.assertEqual(cores, {canonical_c908})
        mnemonics = {r.mnemonic for r in rows}
        self.assertEqual(mnemonics, {"vfadd.vv", "vmul.vv"})
        for row in rows:
            self.assertEqual(row.source_kind, "measured")
            self.assertEqual(row.source, "rvv-bench")
            self.assertEqual(row.architecture, "riscv")
            self.assertEqual(row.applies_to, "mnemonic+lmul")

    def test_ingest_uses_injected_fetch(self):
        import json as _json

        def fake_fetch(url: str) -> str:
            return _json.dumps(self.PAYLOAD)

        rows = ingest_rvv_bench(fetch=fake_fetch)
        self.assertTrue(rows)

    def test_ingest_network_failure_returns_empty(self):
        def broken(url: str) -> str:
            raise RuntimeError("offline")

        self.assertEqual(ingest_rvv_bench(fetch=broken), [])


if __name__ == "__main__":
    unittest.main()
