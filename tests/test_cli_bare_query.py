"""Tests for bare-query dispatch (issue #2).

``simdref <mnemonic> [--arch ARCH] [--json]`` must:
  - resolve on exact mnemonic match and print to stdout without opening
    the TUI — previously a TTY invocation loaded the 92K-intrinsic TUI
    and hung at >1GB RSS.
  - accept ``--arch`` aliases (sapphirerapids → EMR).
  - emit a structured JSON record with ``latency_cycles`` / ``tput_cpi`` /
    ``ports`` per arch when ``--json`` is passed.
  - tolerate the leading verb ``show`` / ``lookup`` / ``info`` since users
    reach for one even though no such subcommand exists.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest


def _run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"src{os.pathsep}{existing}" if existing else "src"
    env["COLUMNS"] = "200"
    return subprocess.run(
        [sys.executable, "-m", "simdref", *args],
        cwd=".",
        env=env,
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


class BareQueryTests(unittest.TestCase):
    def test_bare_mnemonic_prints_without_tui(self):
        proc = _run_cli("vfmadd213pd")
        out = _strip_ansi(proc.stdout)
        self.assertRegex(out, r"variant")
        self.assertRegex(out, r"lat=\d")
        self.assertRegex(out, r"cpi=\d")

    def test_bare_mnemonic_with_sapphirerapids_alias_pins_to_emr(self):
        proc = _run_cli("vfmadd213pd", "--arch", "sapphirerapids")
        out = _strip_ansi(proc.stdout)
        self.assertIn("EMR", out)
        self.assertRegex(out, r"lat=\d")
        self.assertRegex(out, r"cpi=\d")

    def test_leading_show_verb_is_tolerated(self):
        """The original bug report used ``simdref show vgatherdpd --arch …``;
        there's no ``show`` subcommand, but the verb should be dropped
        rather than hanging in the TUI or reporting 'no match'."""
        proc = _run_cli("show", "vgatherdpd", "--arch", "sapphirerapids")
        out = _strip_ansi(proc.stdout)
        self.assertIn("EMR", out)
        self.assertRegex(out, r"lat=\d")

    def test_bare_query_json_emits_structured_lat_cpi(self):
        proc = _run_cli("vfmadd213pd", "--arch", "sapphirerapids", "--json")
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["arch"], "EMR")
        self.assertGreaterEqual(len(payload["variants"]), 1)
        variant = payload["variants"][0]
        emr_rows = [r for r in variant["per_arch"] if r["arch"] == "EMR"]
        self.assertEqual(len(emr_rows), 1)
        self.assertIsNotNone(emr_rows[0]["latency_cycles"])
        self.assertIsNotNone(emr_rows[0]["tput_cpi"])

    def test_unknown_arch_exits_nonzero(self):
        proc = _run_cli("vfmadd213pd", "--arch", "not_a_core", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not in the local catalog", _strip_ansi(proc.stderr))

    def test_unknown_mnemonic_in_non_tty_exits_2(self):
        proc = _run_cli("thismnemonicdoesnotexist", check=False)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
