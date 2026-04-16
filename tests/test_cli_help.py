import os
import re
import subprocess
import sys
import unittest


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
        self.assertIn("simdref update --build-local", output)
        self.assertIn("simdref update --offline", output)
        self.assertIn("--with-sdm", output)


if __name__ == "__main__":
    unittest.main()
