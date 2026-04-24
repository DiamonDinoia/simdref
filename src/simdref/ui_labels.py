"""Single source of truth for UI labels + keymap shared by TUI and web SPA.

Both surfaces import ``UI_LABELS`` and ``KEYMAP`` from this module (the web
SPA gets it injected as a JSON blob at template-render time in
``simdref.web``), so terminology can never drift between them.

Add new labels or bindings here first, then reference them from ``tui.py``
or ``app.js``. A parity test asserts both consumers cover the full action
set — see ``tests/test_ui_labels_parity.py``.
"""

from __future__ import annotations

from typing import Final


# Canonical labels. Plan rule: ends the "Agg"/"asm"/"modeled" drift.
UI_LABELS: Final[dict[str, str]] = {
    # Kinds
    "kind_intrinsic": "Intrinsics",
    "kind_instruction": "Instructions",
    # Microarch aggregation
    "aggregation": "Aggregation",
    # Perf source labels — "Modeled (Intel)" instead of bare "modeled"
    "source_measured": "Measured",
    "source_modeled": "Modeled (Intel)",
    # Toolbar labels
    "arch": "Arch",
    "isa": "ISA",
    "category": "Category",
    "perf": "Perf",
    # Status text
    "no_matches": "No matches",
    "results_for": "results for",
    # Toolbar checkboxes
    "toggle_perf": "perf",
    "toggle_docs": "docs",
    "toggle_modeled": "include modeled",
}


# Keymap: action -> (primary_key, description). Both surfaces must cover
# every key in this table (parity test enforces).
KEYMAP: Final[dict[str, tuple[str, str]]] = {
    "focus_search": ("/", "Focus search"),
    "focus_search_alt": ("ctrl+k", "Focus search"),
    "toggle_filter_drawer": ("f", "Toggle filter drawer"),
    "cycle_kind": ("k", "Cycle kind"),
    "cycle_arch": ("a", "Cycle arch"),
    "next_result": ("j", "Next result"),
    "prev_result": ("k", "Previous result"),
    "open_detail": ("enter", "Open detail section"),
    "switch_tab": ("tab", "Switch Search/Annotate"),
    "help": ("?", "Help"),
    "quit_or_close": ("escape", "Quit / close drawer"),
}


def keymap_actions() -> frozenset[str]:
    """Return the canonical action-name set used for parity checks."""
    return frozenset(KEYMAP.keys())


def as_json_dict() -> dict:
    """Projection used by the web template injector."""
    return {
        "labels": dict(UI_LABELS),
        "keymap": {action: list(pair) for action, pair in KEYMAP.items()},
    }
