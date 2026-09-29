"""Parity checks between ``simdref.ui_labels`` and the TUI.

The plan requires that TUI and web use the same vocabulary and key actions.
These tests enforce the contract on the TUI side; the web SPA lives in the
``simdref-web`` repo and enforces its own half of the contract there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from simdref.ui_labels import UI_LABELS, as_json_dict, keymap_actions

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "simdref"


def _read(name: str) -> str:
    return (_SRC_ROOT / name).read_text(encoding="utf-8")


def test_ui_labels_cover_required_concepts() -> None:
    required = {
        "kind_intrinsic",
        "kind_instruction",
        "aggregation",
        "source_measured",
        "source_modeled",
        "arch",
        "isa",
        "no_matches",
        "results_for",
    }
    missing = required - set(UI_LABELS)
    assert not missing, f"UI_LABELS missing keys: {sorted(missing)}"


def test_keymap_has_canonical_actions() -> None:
    required = {
        "focus_search",
        "toggle_filter_drawer",
        "cycle_kind",
        "cycle_arch",
        "next_result",
        "prev_result",
        "open_detail",
        "switch_tab",
        "help",
        "quit_or_close",
    }
    missing = required - keymap_actions()
    assert not missing, f"KEYMAP missing actions: {sorted(missing)}"


def test_as_json_dict_is_serialisable() -> None:
    blob = json.dumps(as_json_dict())
    parsed = json.loads(blob)
    assert parsed["labels"]["aggregation"] == "Aggregation"
    assert parsed["keymap"]["focus_search"][0] == "/"


# ---------------------------------------------------------------------------
# Drift guards: the forbidden literals the plan calls out by name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "surface,needles",
    [
        # TUI: the Annotate toolbar used to say "Agg"; the kind toggle said "asm".
        (
            "tui.py",
            {
                r'Label\("Agg"\)': '"Agg" Label — use UI_LABELS["aggregation"]',
                r'KindToggle\("instruction",\s*"asm"': '"asm" kind label — use UI_LABELS["kind_instruction"]',
            },
        ),
    ],
)
def test_no_drifted_literals(surface: str, needles: dict) -> None:
    text = _read(surface)
    for pattern, reason in needles.items():
        assert not re.search(pattern, text), f"{surface}: {reason}"
