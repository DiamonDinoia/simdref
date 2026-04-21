"""Tests for the perf_sources ingesters + canonical core table."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from simdref.models import InstructionRecord
from simdref.perf_sources import (
    CANONICAL_CORES,
    PerfRow,
    canonical_core_id,
    core_architecture,
    merge_perf_rows,
    parse_llvm_mca_json,
)
from simdref.perf_sources.cores import CoreSpec
from simdref.perf_sources.llvm_mca import LLVMMcaError
from simdref.perf_sources.llvm_scheduling import (
    LLVMSchedulingError,
    _build_perf_rows,
    _extract_repeated_chunks,
    _filter_disassembly,
    _parse_exegesis_yaml,
    build_byte_lines,
    collect_core_schedule,
)


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
        self.assertEqual(entry["measurement"], {"TP": "0.5", "TP_loop": "0.5"})
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
            mnemonic="FMLA", core="neoverse-n1", source="llvm-mca",
            source_kind="measured", source_version="llvm-mca@abc",
            architecture="arm", latency="3",
        )
        merge_perf_rows([record], [row], overwrite=True)
        self.assertEqual(record.arch_details["neoverse-n1"]["source_kind"], "measured")

    def test_form_match_narrows_to_one_variant(self):
        a = self._record("arm", "FMLA", form="FMLA V0.4S, V1.4S, V2.4S")
        b = self._record("arm", "FMLA", form="FMLA V0.2D, V1.2D, V2.2D")
        row = PerfRow(
            mnemonic="FMLA", core="neoverse-n1", source="llvm-mca",
            source_kind="measured", source_version="llvm-mca@abc",
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


class LLVMSchedulingTests(unittest.TestCase):
    """Tests the structured exegesis → mc → mca pipeline."""

    def _core(self, canonical: str = "neoverse-n1") -> CoreSpec:
        return next(c for c in CANONICAL_CORES if c.canonical_id == canonical)

    def test_parse_exegesis_yaml_extracts_opcode_and_snippet(self):
        yaml_text = (
            "---\n"
            "key:\n"
            "  instructions:\n"
            "    - 'FADDv4f32 Q0 Q1 Q2'\n"
            "assembled_snippet: AABBCCDDDEADBEEFDEADBEEFDEADBEEFDEADBEEFCAFEBABE\n"
            "...\n"
        )
        entries = _parse_exegesis_yaml(yaml_text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["opcode"], "FADDv4f32")
        self.assertIn("DEADBEEF", entries[0]["snippet"])

    def test_build_byte_lines_extracts_opcode_from_entries(self):
        entries = [
            {
                "opcode": "FADDv4f32",
                "snippet": "AABBCCDD" + "DEADBEEF" * 4 + "CAFEBABE",
            }
        ]
        lines = build_byte_lines(entries, "aarch64")
        self.assertEqual(lines, ["0xDE 0xAD 0xBE 0xEF"])

    def test_extract_repeated_chunks_consecutive(self):
        snippet = "AABBCCDD" + "DEADBEEF" * 4 + "CAFEBABE"
        chunks = {c.hex().upper() for c in _extract_repeated_chunks(snippet, "aarch64")}
        self.assertIn("DEADBEEF", chunks)

    def test_extract_repeated_chunks_alternating_dep_breaker(self):
        # Prologue + (target, breaker) × 4 + epilogue. Neither pattern
        # repeats *consecutively* but both repeat by total count.
        snippet = (
            "EA0F1FFC05008052"
            + ("AA0C014E" + "4C2D010E") * 4
            + "EA0741FCC0035FD6"
        )
        chunks = {c.hex().upper() for c in _extract_repeated_chunks(snippet, "aarch64")}
        self.assertIn("AA0C014E", chunks)  # dup v10.16b, w5 (target)
        self.assertIn("4C2D010E", chunks)  # smov w12, v10.b[0] (breaker)

    def test_extract_repeated_chunks_riscv(self):
        snippet = "57730018" + "D79B734F" * 4 + "8280"
        chunks = {c.hex().upper() for c in _extract_repeated_chunks(snippet, "riscv")}
        self.assertIn("D79B734F", chunks)

    def test_extract_repeated_chunks_empty_when_nothing_repeats(self):
        self.assertEqual(_extract_repeated_chunks("AABBCCDD", "aarch64"), [])

    def test_filter_disassembly_drops_directives_and_comments(self):
        raw = "\t.text\n\tfadd\tv0.4s, v1.4s, v2.4s\n\t# comment\n\tadd\tx0, x1, x2\n"
        filtered = _filter_disassembly(raw)
        self.assertIn("fadd", filtered)
        self.assertIn("add", filtered)
        self.assertNotIn(".text", filtered)
        self.assertNotIn("# comment", filtered)

    def test_build_perf_rows_joins_asm_and_info(self):
        payload = {
            "CodeRegions": [
                {
                    "InstructionInfoView": {
                        "InstructionList": [
                            {"Latency": 2, "RThroughput": 0.5},
                            {"Latency": 1, "RThroughput": 0.333},
                            {"Latency": 4, "RThroughput": 0.5},
                        ]
                    },
                }
            ]
        }
        asm_lines = [
            "\tfadd\tv0.4s, v1.4s, v2.4s",
            "\tadd\tx0, x1, x2",
            "\tfmla\tv0.4s, v1.4s, v2.4s",
        ]
        rows = _build_perf_rows(
            payload, asm_lines, core=self._core(), mca_version="22.1.3"
        )
        self.assertEqual(len(rows), 3)
        mnemonics = {row.mnemonic for row in rows}
        self.assertEqual(mnemonics, {"FADD", "ADD", "FMLA"})
        fadd = next(r for r in rows if r.mnemonic == "FADD")
        self.assertEqual(fadd.latency, "2")
        self.assertEqual(fadd.cpi, "0.5")
        self.assertEqual(fadd.core, "neoverse-n1")
        self.assertEqual(fadd.source_kind, "modeled")
        self.assertEqual(fadd.architecture, "arm")

    def test_build_perf_rows_populates_uops_ports_and_kind(self):
        payload = {
            "TargetInfo": {
                "Resources": [
                    "N1UnitV0",
                    "N1UnitV1",
                    "N1UnitV01",
                    "N1UnitL01",
                ]
            },
            "CodeRegions": [
                {
                    "InstructionInfoView": {
                        "InstructionList": [
                            {
                                "Latency": 3,
                                "RThroughput": 0.5,
                                "NumMicroOpcodes": 1,
                                "mayLoad": False,
                                "mayStore": False,
                            },
                            {
                                "Latency": 4,
                                "RThroughput": 1.0,
                                "NumMicroOpcodes": 2,
                                "mayLoad": True,
                                "mayStore": False,
                            },
                        ]
                    },
                    "ResourcePressureView": {
                        "ResourcePressureInfo": [
                            {"InstructionIndex": 0, "ResourceIndex": 0, "ResourceUsage": 0.5},
                            {"InstructionIndex": 0, "ResourceIndex": 1, "ResourceUsage": 0.5},
                            {"InstructionIndex": 0, "ResourceIndex": 2, "ResourceUsage": 0.0},
                            {"InstructionIndex": 1, "ResourceIndex": 3, "ResourceUsage": 1.0},
                        ]
                    },
                }
            ]
        }
        asm_lines = [
            "\tfadd\tv0.4s, v1.4s, v2.4s",
            "\tldr\tq0, [x1]",
        ]
        rows = _build_perf_rows(
            payload, asm_lines, core=self._core(), mca_version="22.1.3"
        )
        self.assertEqual(len(rows), 2)
        fadd = next(r for r in rows if r.mnemonic == "FADD")
        self.assertEqual(fadd.extra_measurement["uops"], "1")
        self.assertEqual(fadd.extra_measurement["ports"], "0.50*V0 0.50*V1")
        self.assertNotIn("kind", fadd.extra_measurement)
        ldr = next(r for r in rows if r.mnemonic == "LDR")
        self.assertEqual(ldr.extra_measurement["uops"], "2")
        self.assertEqual(ldr.extra_measurement["ports"], "1.00*L01")
        self.assertEqual(ldr.extra_measurement["kind"], "load")

    def test_build_perf_rows_risc_v_passes_through_resource_names(self):
        payload = {
            "TargetInfo": {"Resources": ["SiFive7ALU"]},
            "CodeRegions": [
                {
                    "InstructionInfoView": {
                        "InstructionList": [
                            {"Latency": 1, "RThroughput": 1.0, "NumMicroOpcodes": 1},
                        ]
                    },
                    "ResourcePressureView": {
                        "ResourcePressureInfo": [
                            {"InstructionIndex": 0, "ResourceIndex": 0, "ResourceUsage": 1.0},
                        ]
                    },
                }
            ]
        }
        rows = _build_perf_rows(
            payload,
            ["\tadd\tx1, x1, x2"],
            core=self._core(),
            mca_version="22",
        )
        self.assertEqual(rows[0].extra_measurement["ports"], "1.00*SiFive7ALU")

    def test_build_perf_rows_normalises_sub_unit_resource_names(self):
        # llvm-mca encodes sub-units of a replicated resource with
        # non-printable suffix bytes (``N1UnitD.\x00``); these must
        # become ``D0`` / ``D1`` so the port column stays readable.
        payload = {
            "TargetInfo": {
                "Resources": [
                    "N1UnitD.\x00",
                    "N1UnitD.\x01",
                ]
            },
            "CodeRegions": [
                {
                    "InstructionInfoView": {
                        "InstructionList": [
                            {"Latency": 1, "RThroughput": 1.0, "NumMicroOpcodes": 2},
                        ]
                    },
                    "ResourcePressureView": {
                        "ResourcePressureInfo": [
                            {"InstructionIndex": 0, "ResourceIndex": 0, "ResourceUsage": 1.0},
                            {"InstructionIndex": 0, "ResourceIndex": 1, "ResourceUsage": 1.0},
                        ]
                    },
                }
            ]
        }
        rows = _build_perf_rows(
            payload,
            ["\tadd\tx1, x1, x2"],
            core=self._core(),
            mca_version="22",
        )
        self.assertEqual(rows[0].extra_measurement["ports"], "1.00*D0 1.00*D1")

    def test_build_perf_rows_writes_tp_loop_into_arch_details(self):
        payload = {
            "TargetInfo": {"Resources": ["N1UnitV0"]},
            "CodeRegions": [
                {
                    "InstructionInfoView": {
                        "InstructionList": [
                            {"Latency": 2, "RThroughput": 0.5, "NumMicroOpcodes": 1},
                        ]
                    },
                    "ResourcePressureView": {
                        "ResourcePressureInfo": [
                            {"InstructionIndex": 0, "ResourceIndex": 0, "ResourceUsage": 0.5},
                        ]
                    },
                }
            ]
        }
        rows = _build_perf_rows(
            payload,
            ["\tfadd\tv0.4s, v1.4s, v2.4s"],
            core=self._core(),
            mca_version="22",
        )
        entry = rows[0].as_arch_details_entry()
        # CPI must land under ``TP_loop`` (the key the measurement table
        # renders as the "CPI" column); ``TP`` stays as a compatibility
        # duplicate for older consumers.
        self.assertEqual(entry["measurement"]["TP_loop"], "0.5")
        self.assertEqual(entry["measurement"]["TP"], "0.5")
        self.assertEqual(entry["measurement"]["uops"], "1")
        self.assertEqual(entry["measurement"]["ports"], "0.50*V0")
        self.assertEqual(entry["source_kind"], "modeled")

    def test_build_perf_rows_dedupes_by_mnemonic(self):
        payload = {
            "CodeRegions": [
                {
                    "InstructionInfoView": {
                        "InstructionList": [
                            {"Latency": 2, "RThroughput": 0.5},
                            {"Latency": 3, "RThroughput": 0.5},
                        ]
                    }
                }
            ]
        }
        asm_lines = [
            "\tfadd\tv0.4s, v1.4s, v2.4s",
            "\tfadd\tv0.2d, v1.2d, v2.2d",
        ]
        rows = _build_perf_rows(
            payload, asm_lines, core=self._core(), mca_version="v"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mnemonic, "FADD")

    def test_collect_core_schedule_uses_cache_when_present(self):
        core = self._core()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            cache_dir = cache_root / core.llvm_triple / core.llvm_cpu
            cache_dir.mkdir(parents=True)
            (cache_dir / "exegesis.yaml").write_text(
                "---\n"
                "key:\n"
                "  instructions:\n"
                "    - 'FADDv4f32 Q0 Q1 Q2'\n"
                "assembled_snippet: "
                + "AABBCCDD" + "DEADBEEF" * 4 + "CAFEBABE\n"
                + "...\n"
            )
            (cache_dir / "disassembly.s").write_text(
                "\tfadd\tv0.4s, v1.4s, v2.4s\n"
            )
            # payload below — the mca.json cache file
            (cache_dir / "mca.json").write_text(
                json.dumps(
                    {
                        "CodeRegions": [
                            {
                                "Instructions": [
                                    "\tfadd\tv0.4s, v1.4s, v2.4s"
                                ],
                                "InstructionInfoView": {
                                    "InstructionList": [
                                        {"Latency": 3, "RThroughput": 0.5}
                                    ]
                                },
                            }
                        ]
                    }
                )
            )
            # All three cache files exist, so no subprocess should run.
            with mock.patch(
                "subprocess.run", side_effect=AssertionError("no subprocess expected")
            ):
                rows = collect_core_schedule(
                    core, cache_root=cache_root, mca_version="22.1.3"
                )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mnemonic, "FADD")
        self.assertEqual(rows[0].latency, "3")

    def test_collect_core_schedule_raises_on_empty_exegesis(self):
        core = self._core()
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            cache_dir = cache_root / core.llvm_triple / core.llvm_cpu
            cache_dir.mkdir(parents=True)
            (cache_dir / "exegesis.yaml").write_text("# empty file\n")
            with self.assertRaises(LLVMSchedulingError):
                collect_core_schedule(
                    core, cache_root=cache_root, mca_version="22.1.3"
                )

    def test_llvm_mca_error_type_exists(self):
        self.assertTrue(issubclass(LLVMMcaError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
