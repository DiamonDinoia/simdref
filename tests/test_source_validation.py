import subprocess
import sys
import unittest


class SourceValidationTests(unittest.TestCase):
    def test_offline_source_validation_script_passes(self):
        result = subprocess.run(
            [sys.executable, "tools/validate_sources.py", "--offline"],
            cwd=".",
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("source validation passed", result.stdout)

    def test_live_source_validation_script_passes(self):
        result = subprocess.run(
            [sys.executable, "tools/validate_sources.py"],
            cwd=".",
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("source validation passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
