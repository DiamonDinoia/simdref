"""Tests for the ``simdref show`` subcommand (issue #2).

Before this command existed, ``simdref show <mnemonic> --arch sapphirerapids``
fell into the bare-query smart-lookup path, which opened the TUI in TTY mode
(hang at >1GB RSS) or emitted ``no instruction match`` in non-TTY mode
(because the query ``'show vgatherdpd --arch sapphirerapids'`` was treated
as one string).
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
    )


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


class ShowCommandTests(unittest.TestCase):
    def test_show_exists_and_prints_mnemonic_data(self):
        """``simdref show vfmadd213pd`` should print docs + aggregated perf."""
        proc = _run_cli("show", "vfmadd213pd")
        out = _strip_ansi(proc.stdout)
        self.assertIn("vfmadd213pd", out.lower())
        self.assertRegex(out, r"variant", "output should mention variants")
        # Aggregated summary line must have concrete numbers, not the
        # '-c cpi=-' placeholder that issue #2 reported.
        self.assertRegex(out, r"lat=\d")
        self.assertRegex(out, r"cpi=\d")

    def test_show_with_sapphirerapids_alias_pins_to_emr(self):
        """``--arch sapphirerapids`` must resolve to EMR (uops.info has no
        distinct SPR row; EMR shares the Golden Cove P-core)."""
        proc = _run_cli("show", "vfmadd213pd", "--arch", "sapphirerapids")
        out = _strip_ansi(proc.stdout)
        self.assertIn("EMR", out)
        self.assertRegex(out, r"lat=\d")
        self.assertRegex(out, r"cpi=\d")

    def test_show_json_returns_structured_lat_cpi(self):
        """``--json`` emits a machine-readable record with numeric fields
        (feature request in issue #2)."""
        proc = _run_cli("show", "vfmadd213pd", "--arch", "sapphirerapids", "--json")
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["mnemonic"], "VFMADD213PD")
        self.assertEqual(payload["arch"], "EMR")
        self.assertGreaterEqual(len(payload["variants"]), 1)
        variant = payload["variants"][0]
        self.assertIn("aggregate", variant)
        self.assertIn("per_arch", variant)
        emr_rows = [r for r in variant["per_arch"] if r["arch"] == "EMR"]
        self.assertEqual(len(emr_rows), 1)
        self.assertIsNotNone(emr_rows[0]["latency_cycles"])
        self.assertIsNotNone(emr_rows[0]["tput_cpi"])

    def test_show_unknown_arch_exits_nonzero(self):
        proc = _run_cli("show", "vfmadd213pd", "--arch", "not_a_core", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not in the local catalog", _strip_ansi(proc.stderr))

    def test_show_unknown_mnemonic_exits_code_2(self):
        proc = _run_cli("show", "thismnemonicdoesnotexist", check=False)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
