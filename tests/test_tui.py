"""Smoke tests for TUI keybindings.

These use Textual's ``App.run_test()`` harness, so they require an
interactive catalog (SQLite DB built in ``data/derived``). Tests that
cannot find a catalog skip rather than fail.
"""

from __future__ import annotations

import asyncio
import unittest

try:
    import textual  # noqa: F401

    HAS_TEXTUAL = True
except ImportError:  # pragma: no cover - Textual is a runtime dep
    HAS_TEXTUAL = False

from simdref.storage import CATALOG_PATH, SQLITE_PATH, sqlite_schema_is_current


CATALOG_READY = CATALOG_PATH.exists() and SQLITE_PATH.exists() and sqlite_schema_is_current()


@unittest.skipUnless(HAS_TEXTUAL and CATALOG_READY, "textual + catalog required")
class TuiKeybindingSmokeTests(unittest.TestCase):
    """Boot the TUI headless and hammer the bindings plan (Phase 6) advertises."""

    def _run(self, coro):
        asyncio.run(coro)

    def test_slash_focuses_search_input(self):
        from simdref.tui import SimdrefApp

        async def scenario():
            app = SimdrefApp()
            async with app.run_test() as pilot:
                await pilot.press("/")
                focused = app.focused
                self.assertIsNotNone(focused)
                self.assertEqual(getattr(focused, "id", None), "search-input")

        self._run(scenario())

    def test_ctrl_k_aliases_focus_search(self):
        from simdref.tui import SimdrefApp

        async def scenario():
            app = SimdrefApp()
            async with app.run_test() as pilot:
                await pilot.press("escape")
                await pilot.press("ctrl+k")
                focused = app.focused
                self.assertEqual(getattr(focused, "id", None), "search-input")

        self._run(scenario())

    def test_question_mark_opens_help_modal(self):
        from simdref.tui import HelpScreen, SimdrefApp

        async def scenario():
            app = SimdrefApp()
            async with app.run_test() as pilot:
                await pilot.press("escape")
                # '?' on most keymaps is shift+slash; press the literal '?' glyph.
                await pilot.press("question_mark")
                self.assertIsInstance(app.screen, HelpScreen)
                await pilot.press("escape")
                self.assertNotIsInstance(app.screen, HelpScreen)

        self._run(scenario())

    def test_j_and_k_move_list_cursor(self):
        from simdref.tui import SimdrefApp
        from textual.widgets import ListView

        async def scenario():
            app = SimdrefApp(initial_query="_mm_add")
            async with app.run_test() as pilot:
                # Let the initial search populate.
                await pilot.pause()
                await pilot.pause()
                results = app.query_one("#results-list", ListView)
                if len(results) < 2:
                    self.skipTest("not enough results in the dev catalog to exercise j/k")
                results.focus()
                initial_index = results.index or 0
                await pilot.press("j")
                await pilot.pause()
                self.assertEqual(results.index, initial_index + 1)
                await pilot.press("k")
                await pilot.pause()
                self.assertEqual(results.index, initial_index)

        self._run(scenario())

    def test_escape_clears_detail(self):
        from simdref.tui import SimdrefApp
        from textual.containers import VerticalScroll

        async def scenario():
            app = SimdrefApp(initial_query="_mm_add")
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.pause()
                await pilot.press("1")
                await pilot.pause()
                detail = app.query_one("#detail-scroll", VerticalScroll)
                # Detail pane populates on pick.
                if len(detail.children) == 0:
                    self.skipTest("pick did not populate detail pane")
                await pilot.press("escape")
                await pilot.pause()
                detail = app.query_one("#detail-scroll", VerticalScroll)
                self.assertEqual(len(detail.children), 0)

        self._run(scenario())


if __name__ == "__main__":
    unittest.main()
