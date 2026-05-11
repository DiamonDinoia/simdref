"""Regression tests for the web app's search-index / bucket code.

Driven through Node — the production code in
``src/simdref/templates/app.js`` is loaded into a vm sandbox, then
exercised with a small fixture catalog. Skips silently when ``node`` is
not on PATH so non-web environments stay green.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
HARNESS = Path(__file__).parent / "web_assets" / "run_search_index_check.mjs"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_phase1_and_phase2_search_invariants() -> None:
    assert NODE is not None  # narrow for type-checker
    result = subprocess.run(
        [NODE, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0, (
        f"app.js search-index check failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
