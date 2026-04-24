"""Preset persistence for the TUI + web.

Plan rule (follow-up on "ui redesign"): default to the ``intel`` preset on
first launch, then remember whatever the user last picked.

The TUI stores the last preset in ``$XDG_STATE_HOME/simdref/last-preset``
(falling back to ``~/.local/state/simdref/last-preset``). The web stores
it in ``localStorage["simdref-last-preset"]`` — exercised by asserting the
JS reads it with the right precedence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simdref import cli


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    # Make sure the freshly-redirected path is empty.
    assert not cli._tui_preset_pref_path().exists()
    yield tmp_path


def test_load_last_preset_returns_none_on_first_run(tmp_state):
    assert cli._load_last_preset() is None


def test_save_then_load_roundtrip(tmp_state):
    cli._save_last_preset("arm64")
    assert cli._load_last_preset() == "arm64"
    cli._save_last_preset("intel")
    assert cli._load_last_preset() == "intel"


def test_run_tui_uses_state_when_no_explicit_preset(tmp_state, monkeypatch):
    """Regression: ``isa`` with no args must read the persisted preset, not
    force ``intel``. Previously the default-main dispatch passed ``"intel"``
    directly to ``_run_tui``, masking the state file."""
    cli._save_last_preset("arm64")
    seen: dict = {}

    def fake_run_tui(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("simdref.tui.run_tui", fake_run_tui)
    cli._run_tui()  # no explicit preset
    assert seen["initial_preset"] == "arm64"


def test_run_tui_explicit_preset_beats_state(tmp_state, monkeypatch):
    cli._save_last_preset("arm64")
    seen: dict = {}

    def fake_run_tui(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("simdref.tui.run_tui", fake_run_tui)
    cli._run_tui(initial_preset="riscv")
    assert seen["initial_preset"] == "riscv"


def test_run_tui_falls_back_to_intel_on_first_run(tmp_state, monkeypatch):
    seen: dict = {}

    def fake_run_tui(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("simdref.tui.run_tui", fake_run_tui)
    cli._run_tui()
    assert seen["initial_preset"] == "intel"


def test_save_is_best_effort_on_readonly_state(tmp_path, monkeypatch):
    """Persistence must never raise — a broken state dir can't block the TUI."""
    # Point XDG_STATE_HOME at an existing *file* so the mkdir fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    # Must not raise.
    cli._save_last_preset("intel")
    assert cli._load_last_preset() is None


# ---------------------------------------------------------------------------
# Web: the JS must prefer URL param > localStorage > "intel" default.
# ---------------------------------------------------------------------------


def test_web_preset_precedence_in_app_js():
    text = (Path(__file__).resolve().parent.parent
            / "src" / "simdref" / "templates" / "app.js").read_text()
    # localStorage read key
    assert 'localStorage.getItem("simdref-last-preset")' in text
    # URL-param check precedes localStorage read, and the fallback is
    # "intel" — all three must appear in the same precedence block.
    url_idx = text.index('params.get("preset")')
    storage_idx = text.index('localStorage.getItem("simdref-last-preset")')
    fallback_idx = text.index('ARCH_PRESETS["intel"] ? "intel"')
    assert url_idx < storage_idx < fallback_idx, (
        "Web preset precedence must be: URL param > localStorage > intel"
    )


def test_web_persists_last_preset_on_click():
    text = (Path(__file__).resolve().parent.parent
            / "src" / "simdref" / "templates" / "app.js").read_text()
    assert 'localStorage.setItem("simdref-last-preset"' in text
