import json
import tempfile
import unittest
from pathlib import Path

from simdref.ingest import build_catalog
from simdref.lsp import _completion_candidates, _hover_markdown
from simdref.storage import build_sqlite, save_catalog, open_db
from simdref.web import export_web


class LspWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp_path = Path(cls._tmpdir.name)
        cls._catalog = build_catalog(offline=True)
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

    def test_completion_returns_intrinsics(self):
        items = _completion_candidates(self._conn, "_mm256_a", limit=5)
        labels = [item["label"] for item in items]
        self.assertIn("_mm256_add_ps", labels)

    def test_export_web_produces_expected_files(self):
        catalog = build_catalog(offline=True)
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
            # Search index instructions have key but no measurements
            instr = search["instructions"][0]
            self.assertIn("key", instr)
            self.assertIn("display_key", instr)
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
            self.assertTrue(saw_sdm)

            # Intrinsic details
            intr_details = json.loads(
                (Path(tmpdir) / "intrinsic-details.json").read_text()
            )
            self.assertIsInstance(intr_details, dict)
            self.assertTrue(len(intr_details) > 0)


if __name__ == "__main__":
    unittest.main()
