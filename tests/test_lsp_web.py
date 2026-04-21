import json
import msgpack
import tempfile
import unittest
from pathlib import Path

from simdref.ingest import build_catalog
from simdref.lsp import (
    _completion_candidates,
    _hover_markdown,
    _normalise_architectures,
    load_instruction_best_form,
)
from simdref.storage import build_sqlite, save_catalog, open_db
from simdref.web import export_web
from conftest import build_fixture_catalog


class LspWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp_path = Path(cls._tmpdir.name)
        cls._catalog = build_fixture_catalog()
        save_catalog(cls._catalog, path=tmp_path / "catalog.msgpack")
        build_sqlite(cls._catalog, path=tmp_path / "catalog.db")
        cls._conn = open_db(path=tmp_path / "catalog.db")

    @classmethod
    def tearDownClass(cls):
        cls._conn.close()
        cls._tmpdir.cleanup()

    def test_intrinsic_hover_contains_signature(self):
        markdown = _hover_markdown(self._conn, "_mm256_add_ps")
        self.assertIsNotNone(markdown)
        self.assertIn("_mm256_add_ps", markdown)
        self.assertIn("Instructions:", markdown)

    def test_riscv_instruction_hover_supports_dotted_mnemonic(self):
        markdown = _hover_markdown(self._conn, "vadd.vv")
        self.assertIsNotNone(markdown)
        self.assertIn("vadd.vv", markdown)
        self.assertIn("ISA V", markdown)

    def test_sqlite_runtime_preserves_riscv_counts_sections_and_policy_metadata(self):
        intrinsic_count = self._conn.execute(
            "SELECT COUNT(*) FROM intrinsics_data WHERE architecture = 'riscv'"
        ).fetchone()[0]
        instruction_count = self._conn.execute(
            "SELECT COUNT(*) FROM instructions_data WHERE architecture = 'riscv'"
        ).fetchone()[0]
        self.assertEqual(intrinsic_count, 20)
        self.assertEqual(instruction_count, 20)

        instruction_payload = msgpack.unpackb(
            self._conn.execute(
                "SELECT payload FROM instructions_data WHERE key = ?",
                ("vsub.vv [masked]",),
            ).fetchone()[0],
            raw=False,
        )
        self.assertIn("Description", instruction_payload["description"])
        self.assertIn("Operation", instruction_payload["description"])
        self.assertEqual(instruction_payload["metadata"]["policy"], "agnostic")
        self.assertEqual(instruction_payload["metadata"]["masking"], "masked")
        self.assertEqual(instruction_payload["metadata"]["tail_policy"], "agnostic")
        self.assertEqual(instruction_payload["metadata"]["mask_policy"], "agnostic")

        intrinsic_payload = msgpack.unpackb(
            self._conn.execute(
                "SELECT payload FROM intrinsics_data WHERE name = ?",
                ("__riscv_vsub_vv_i32m1_m",),
            ).fetchone()[0],
            raw=False,
        )
        self.assertEqual(intrinsic_payload["instructions"], ["vsub.vv [masked]"])
        self.assertEqual(intrinsic_payload["metadata"]["policy"], "agnostic")
        self.assertEqual(intrinsic_payload["metadata"]["masking"], "masked")
        self.assertEqual(intrinsic_payload["metadata"]["tail_policy"], "agnostic")
        self.assertEqual(intrinsic_payload["metadata"]["mask_policy"], "agnostic")

    def test_completion_returns_intrinsics(self):
        items = _completion_candidates(self._conn, "_mm256_a", limit=5)
        labels = [item["label"] for item in items]
        self.assertIn("_mm256_add_ps", labels)

    def test_hover_hides_perf_when_metrics_disabled(self):
        markdown = _hover_markdown(
            self._conn, "_mm256_add_ps", show_perf_metrics=False
        )
        self.assertIsNotNone(markdown)
        self.assertNotIn("Performance:", markdown)

    def test_hover_arch_filter_drops_non_x86_perf(self):
        architectures = _normalise_architectures(["x86"])
        markdown = _hover_markdown(
            self._conn, "vaddq_u8", architectures=architectures
        )
        self.assertIsNotNone(markdown)
        # vaddq_u8 is an Arm Neon intrinsic; restricting perf to x86 leaves
        # nothing to report so the Performance: line must disappear.
        self.assertNotIn("Performance:", markdown)

    def test_asm_file_bias_resolves_mnemonic_to_instruction(self):
        markdown = _hover_markdown(
            self._conn, "vadd.vv", prefer_instruction=True
        )
        self.assertIsNotNone(markdown)
        self.assertIn("vadd.vv", markdown)
        self.assertIn("ISA V", markdown)

    def test_load_instruction_best_form_resolves_bare_mnemonic(self):
        record = load_instruction_best_form(self._conn, "vadd.vv")
        self.assertIsNotNone(record)
        self.assertEqual(record.mnemonic, "vadd.vv")

    def test_export_web_produces_expected_files(self):
        catalog = build_fixture_catalog()
        catalog.instructions[0].pdf_refs = [{
            "source_id": "intel-sdm",
            "label": "Intel SDM",
            "url": "https://example.com/intel-sdm.pdf#page=42",
            "page_start": "42",
            "page_end": "43",
        }]
        catalog.instructions[0].metadata["intel-sdm-url"] = "https://example.com/intel-sdm.pdf#page=42"
        catalog.instructions[0].metadata["intel-sdm-page-start"] = "42"
        catalog.instructions[0].metadata["intel-sdm-page-end"] = "43"
        with tempfile.TemporaryDirectory() as tmpdir:
            export_web(catalog, Path(tmpdir))

            # HTML shell
            html = (Path(tmpdir) / "index.html").read_text()
            self.assertIn("simdref", html)
            self.assertIn("search-index.json", html)

            # Search index
            search = json.loads((Path(tmpdir) / "search-index.json").read_text())
            self.assertIn("isa_config", search)
            self.assertIn("intrinsics", search)
            self.assertIn("instructions", search)
            self.assertTrue(len(search["intrinsics"]) > 0)
            self.assertTrue(len(search["instructions"]) > 0)
            details = json.loads((Path(tmpdir) / "intrinsic-details.json").read_text())
            arm_detail = details["vaddq_u8"]
            self.assertEqual(arm_detail["url"], "https://developer.arm.com/architectures/instruction-sets/intrinsics/vaddq_u8")
            self.assertIn("argument_preparation", arm_detail["metadata"])
            riscv_intr = next(item for item in search["intrinsics"] if item["name"] == "__riscv_vadd_vv_i32m1")
            self.assertEqual(riscv_intr["display_architecture"], "RISC-V")
            riscv_detail = details["__riscv_vadd_vv_i32m1"]
            self.assertEqual(riscv_detail["url"], "https://github.com/riscv-non-isa/riscv-rvv-intrinsic-doc")
            self.assertIn("riscv:vsub.vv", [item["key"] for item in search["instructions"]])
            # Search index instructions have key but no measurements
            instr = search["instructions"][0]
            self.assertIn("key", instr)
            self.assertIn("display_key", instr)
            self.assertIn("architecture", instr)
            self.assertIn("search_fields", instr)
            self.assertNotIn("measurements", instr)

            # Detail chunks directory
            chunks_dir = Path(tmpdir) / "detail-chunks"
            self.assertTrue(chunks_dir.is_dir())
            chunk_files = list(chunks_dir.glob("*.json"))
            self.assertTrue(len(chunk_files) > 0)

            # Spot-check chunks have measurements/operand details and preserve SDM metadata
            saw_sdm = False
            for chunk_file in chunk_files:
                chunk = json.loads(chunk_file.read_text())
                self.assertIsInstance(chunk, dict)
                for detail in chunk.values():
                    self.assertIn("measurements", detail)
                    self.assertIn("operand_details", detail)
                    self.assertIn("architecture", detail)
                    metadata = detail.get("metadata", {})
                    pdf_refs = detail.get("pdf_refs", [])
                    if pdf_refs:
                        self.assertEqual(pdf_refs[0]["source_id"], "intel-sdm")
                        self.assertEqual(pdf_refs[0]["page_start"], "42")
                    if metadata.get("intel-sdm-url"):
                        saw_sdm = True
                        self.assertEqual(metadata["intel-sdm-url"], "https://example.com/intel-sdm.pdf#page=42")
                        self.assertEqual(metadata["intel-sdm-page-start"], "42")
                        self.assertEqual(metadata["intel-sdm-page-end"], "43")
                    if detail["architecture"] == "riscv" and detail["mnemonic"] == "vsub.vv":
                        self.assertIn("Description", detail["description"])
                        self.assertIn("Operation", detail["description"])
            self.assertTrue(saw_sdm)

            # Filter spec: shared source of truth for ISA + category facets
            filter_spec = json.loads((Path(tmpdir) / "filter_spec.json").read_text())
            self.assertIn("family_order", filter_spec)
            self.assertIn("family_sub_order", filter_spec)
            self.assertIn("default_enabled", filter_spec)
            self.assertIn("categories", filter_spec)
            # Every category references a family known to the family_order map.
            known_families = set(filter_spec["family_order"].keys())
            for cat in filter_spec["categories"]:
                self.assertIn(cat["family"], known_families)

            # Build stamp: lets the SPA warn when static bundle ages out of sync
            stamp = json.loads((Path(tmpdir) / "build_stamp.json").read_text())
            self.assertIn("version", stamp)
            self.assertIn("catalog_generated_at", stamp)
            self.assertEqual(stamp["intrinsics"], len(catalog.intrinsics))

            # Intrinsic details
            intr_details = json.loads(
                (Path(tmpdir) / "intrinsic-details.json").read_text()
            )
            self.assertIsInstance(intr_details, dict)
            self.assertTrue(len(intr_details) > 0)
            self.assertIn("doc_sections", intr_details["vaddq_u8"])
            self.assertIn("ACLE Documentation", intr_details["vaddq_u8"]["doc_sections"])

    def test_export_web_preserves_x86_detail_sections_for_rendering(self):
        catalog = build_fixture_catalog()
        x86_instruction = next(item for item in catalog.instructions if item.architecture == "x86" and item.mnemonic == "VPEXPANDD")
        x86_instruction.description = {
            "Description": "Expand packed integers under writemask control.",
            "Operation": "FOR j := 0 TO KL-1",
            "Exceptions": "Type 11 class exceptions.",
            "Intrinsic Equivalents": "_mm512_maskz_expandloadu_epi32",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            export_web(catalog, Path(tmpdir))
            chunk = json.loads((Path(tmpdir) / "detail-chunks" / "VPE.json").read_text())
            detail = chunk[x86_instruction.db_key]
            self.assertIn("description", detail)
            self.assertIn("Description", detail["description"])
            self.assertIn("Operation", detail["description"])
            self.assertIn("Exceptions", detail["description"])
            self.assertIn("Intrinsic Equivalents", detail["description"])
            self.assertTrue(detail["measurements"])


if __name__ == "__main__":
    unittest.main()
