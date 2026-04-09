"""Minimal curses-based terminal UI for interactive search."""

from __future__ import annotations

import curses

from simdref.models import Catalog
from simdref.search import search_catalog


def run_tui(catalog: Catalog) -> int:
    return curses.wrapper(lambda stdscr: _main(stdscr, catalog))


def _main(stdscr, catalog: Catalog) -> int:
    curses.curs_set(1)
    query = ""
    selected = 0
    while True:
        results = search_catalog(catalog, query or "_", limit=15) if query else []
        stdscr.erase()
        stdscr.addstr(0, 0, "simdref TUI  q=quit  Enter=show first-line preview")
        stdscr.addstr(2, 0, f"Query: {query}")
        for index, result in enumerate(results):
            prefix = ">" if index == selected else " "
            stdscr.addstr(4 + index, 0, f"{prefix} [{result.kind}] {result.title} - {result.subtitle[:80]}")
        if results:
            preview = results[selected]
            stdscr.addstr(21, 0, "Preview:")
            stdscr.addstr(22, 0, preview.title[:100])
            stdscr.addstr(23, 0, preview.subtitle[:100])
        stdscr.refresh()

        key = stdscr.get_wch()
        if key in ("q", "\x1b"):
            return 0
        if key == "\n":
            continue
        if key == curses.KEY_UP:
            selected = max(0, selected - 1)
            continue
        if key == curses.KEY_DOWN:
            selected = min(max(0, len(results) - 1), selected + 1)
            continue
        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            query = query[:-1]
            selected = 0
            continue
        if isinstance(key, str) and key.isprintable():
            query += key
            selected = 0
    return 0

