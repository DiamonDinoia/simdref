import json
import tempfile
import unittest
from pathlib import Path

from simdref.ingest import build_catalog
from simdref.lsp import _completion_candidates, _hover_markdown
from simdref.web import export_web


class LspWebTests(unittest.TestCase):
    def test_intrinsic_hover_contains_signature(self):
        catalog = build_catalog(offline=True)
        markdown = _hover_markdown(catalog, "_mm256_add_ps")
        self.assertIsNotNone(markdown)
        self.assertIn("_mm256_add_ps", markdown)
        self.assertIn("Instructions:", markdown)

    def test_completion_returns_intrinsics(self):
        catalog = build_catalog(offline=True)
        items = _completion_candidates(catalog, "_mm256_a", limit=5)
        labels = [item["label"] for item in items]
        self.assertIn("_mm256_add_ps", labels)

    def test_export_web_produces_expected_files(self):
        catalog = build_catalog(offline=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            export_web(catalog, Path(tmpdir))

            # HTML shell
            html = (Path(tmpdir) / "index.html").read_text()
            self.assertIn("simdref", html)
            self.assertIn("search-index.json", html)

            # Search index
            search = json.loads((Path(tmpdir) / "search-index.json").read_text())
            self.assertIn("intrinsics", search)
            self.assertIn("instructions", search)
            self.assertTrue(len(search["intrinsics"]) > 0)
            self.assertTrue(len(search["instructions"]) > 0)
            # Search index instructions have key but no measurements
            instr = search["instructions"][0]
            self.assertIn("key", instr)
            self.assertNotIn("measurements", instr)

            # Detail chunks directory
            chunks_dir = Path(tmpdir) / "detail-chunks"
            self.assertTrue(chunks_dir.is_dir())
            chunk_files = list(chunks_dir.glob("*.json"))
            self.assertTrue(len(chunk_files) > 0)

            # Spot-check a chunk has measurements and operand_details
            chunk = json.loads(chunk_files[0].read_text())
            self.assertIsInstance(chunk, dict)
            for key, detail in chunk.items():
                self.assertIn("measurements", detail)
                self.assertIn("operand_details", detail)
                break

            # Intrinsic details
            intr_details = json.loads(
                (Path(tmpdir) / "intrinsic-details.json").read_text()
            )
            self.assertIsInstance(intr_details, dict)
            self.assertTrue(len(intr_details) > 0)


if __name__ == "__main__":
    unittest.main()
