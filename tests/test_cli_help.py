import os
import re
import subprocess
import sys
import unittest

from simdref.cli import _is_completion_invocation


class CliHelpTests(unittest.TestCase):
    def test_top_level_help_mentions_local_rebuild_commands(self):
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"src{os.pathsep}{existing}" if existing else "src"
        result = subprocess.run(
            [sys.executable, "-m", "simdref", "-h"],
            cwd=".",
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertIn("simdref update --build", output)
        self.assertIn("--with-sdm", output)
        # --offline is gone — users should never see it.
        self.assertNotIn("--offline", output)

    def test_completion_invocation_detection(self):
        self.assertTrue(_is_completion_invocation({"_SIMDREF_COMPLETE": "complete_bash"}))
        self.assertFalse(_is_completion_invocation({"COMP_WORDS": "simdref update --"}))


if __name__ == "__main__":
    unittest.main()
