import subprocess
import sys
import unittest
from pathlib import Path


def _has_local_sdm_material() -> bool:
    return Path("vendor/intel/intel-sdm.pdf").exists() or Path("data/derived/intel-sdm-descriptions.msgpack").exists()


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
        self.assertIn("validated x86 intrinsic links", result.stdout)
        self.assertIn("validated RISC-V intrinsic links", result.stdout)
        self.assertIn("validated RISC-V coverage summary", result.stdout)

    def test_live_source_validation_script_passes(self):
        result = subprocess.run(
            [sys.executable, "tools/validate_sources.py"],
            cwd=".",
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("source validation passed", result.stdout)
        self.assertIn("validated x86 intrinsic links", result.stdout)
        self.assertIn("validated RISC-V intrinsic links", result.stdout)
        self.assertIn("validated RISC-V coverage summary", result.stdout)

    @unittest.skipUnless(_has_local_sdm_material(), "Intel SDM source/cache not available locally")
    def test_live_source_validation_with_required_sdm_passes(self):
        result = subprocess.run(
            [sys.executable, "tools/validate_sources.py", "--require-sdm"],
            cwd=".",
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("validated Intel SDM semantics", result.stdout)
        self.assertIn("validated Intel SDM coverage", result.stdout)
        self.assertIn("source validation passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
