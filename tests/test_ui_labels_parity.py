"""Parity checks between ``simdref.ui_labels`` and both UI surfaces.

The plan requires that TUI and web use the same vocabulary and key actions.
These tests enforce the contract mechanically so the two surfaces cannot
drift silently.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

import pytest

from simdref.ui_labels import KEYMAP, UI_LABELS, as_json_dict, keymap_actions


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "simdref"


def _read(name: str) -> str:
    return (_SRC_ROOT / name).read_text(encoding="utf-8")


def test_ui_labels_cover_required_concepts() -> None:
    required = {
        "kind_intrinsic", "kind_instruction",
        "aggregation",
        "source_measured", "source_modeled",
        "arch", "isa",
        "no_matches", "results_for",
    }
    missing = required - set(UI_LABELS)
    assert not missing, f"UI_LABELS missing keys: {sorted(missing)}"


def test_keymap_has_canonical_actions() -> None:
    required = {
        "focus_search", "toggle_filter_drawer", "cycle_kind", "cycle_arch",
        "next_result", "prev_result", "open_detail", "switch_tab",
        "help", "quit_or_close",
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


@pytest.mark.parametrize("surface,needles", [
    # TUI: the Annotate toolbar used to say "Agg"; the kind toggle said "asm".
    ("tui.py", {
        r'Label\("Agg"\)': '"Agg" Label — use UI_LABELS["aggregation"]',
        r'KindToggle\("instruction",\s*"asm"': '"asm" kind label — use UI_LABELS["kind_instruction"]',
    }),
    # HTML: the kind-bar used to say "asm / instructions".
    ("templates/index.html", {
        r'> asm / instructions<': '"asm / instructions" — use "instructions"',
    }),
])
def test_no_drifted_literals(surface: str, needles: dict) -> None:
    text = _read(surface)
    for pattern, reason in needles.items():
        assert not re.search(pattern, text), f"{surface}: {reason}"


def test_web_template_injects_ui_labels() -> None:
    """``simdref.web._load_template`` must embed a ``window.SIMDREF_UI`` blob."""
    # Import deferred so other tests don't pay the catalog cost.
    from simdref.web import _load_template  # noqa: WPS433

    rendered = _load_template()
    assert "window.SIMDREF_UI" in rendered
    # Labels must round-trip through the injector.
    match = re.search(r"window\.SIMDREF_UI\s*=\s*(\{.*?\});", rendered, re.DOTALL)
    assert match is not None, "SIMDREF_UI assignment not found in template"
    payload = json.loads(match.group(1))
    assert payload["labels"]["kind_instruction"] == "Instructions"


def test_result_row_height_constant_matches_css() -> None:
    """``ROW_HEIGHT_PX`` in app.js must match ``.result { height: Npx }``."""
    css = _read("templates/style.css")
    js = _read("templates/app.js")
    css_match = re.search(r"\.result\s*\{[^}]*?height:\s*(\d+)px", css, re.DOTALL)
    js_match = re.search(r"ROW_HEIGHT_PX\s*=\s*(\d+)", js)
    assert css_match and js_match
    assert css_match.group(1) == js_match.group(1), (
        f"CSS row height {css_match.group(1)}px != JS ROW_HEIGHT_PX {js_match.group(1)}"
    )
