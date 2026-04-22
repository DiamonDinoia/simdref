import os
import re
import subprocess
import sys
import unittest

from simdref.cli import _is_completion_invocation


def _run_cli_help(*args: str) -> str:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"src{os.pathsep}{existing}" if existing else "src"
    # Force a wide terminal so rich doesn't wrap panel labels mid-word.
    env["COLUMNS"] = "200"
    result = subprocess.run(
        [sys.executable, "-m", "simdref", *args],
        cwd=".",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)


class CliHelpTests(unittest.TestCase):
    def test_top_level_help_mentions_new_command_surface(self):
        output = _run_cli_help("-h")
        # The reworked surface promotes 'build' and documents the isa alias.
        self.assertIn("build", output)
        self.assertIn("isa", output)
        # --offline is gone — users should never see it.
        self.assertNotIn("--offline", output)
        # The deprecated 'update --build' phrasing in the banner should be gone.
        self.assertNotIn("simdref update --build", output)
        self.assertNotIn("isa update --build", output)

    def test_top_level_help_shows_commands_and_dev_commands_panels(self):
        output = _run_cli_help("-h")
        self.assertIn("Commands", output)
        self.assertIn("Dev commands", output)

    def test_completion_invocation_detection(self):
        self.assertTrue(_is_completion_invocation({"_SIMDREF_COMPLETE": "complete_bash"}))
        self.assertTrue(_is_completion_invocation({"_ISA_COMPLETE": "complete_bash"}))
        self.assertFalse(_is_completion_invocation({"COMP_WORDS": "simdref update --"}))


class CliCompletionTests(unittest.TestCase):
    def test_completion_help_lists_install_and_show(self):
        output = _run_cli_help("completion", "--help")
        self.assertIn("install", output)
        self.assertIn("show", output)

    def test_completion_show_emits_script(self):
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"src{os.pathsep}{existing}" if existing else "src"
        result = subprocess.run(
            [sys.executable, "-m", "simdref", "completion", "show", "bash"],
            cwd=".",
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("_SIMDREF_COMPLETE", result.stdout)


class CliBuildCommandTests(unittest.TestCase):
    def test_build_help_exposes_with_sdm_and_man_dir(self):
        output = _run_cli_help("build", "--help")
        self.assertIn("--with-sdm", output)
        self.assertIn("--man-dir", output)

    def test_update_help_hides_deprecated_build_flags(self):
        output = _run_cli_help("update", "--help")
        # The deprecated flags are kept for the shim but hidden from help.
        self.assertNotIn("--build", output)
        self.assertNotIn("--with-sdm", output)


if __name__ == "__main__":
    unittest.main()
