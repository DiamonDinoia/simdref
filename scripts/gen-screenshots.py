#!/usr/bin/env python3
"""Regenerate the TUI + web-app screenshots used in README.md.

Images live on the ``docs-assets`` branch so the ``main`` branch stays
lightweight to clone. After running this script, commit the outputs on
that branch:

    git switch docs-assets              # or: git checkout --orphan docs-assets
    mkdir -p img
    cp /tmp/simdref-tui.svg img/tui.svg
    cp /tmp/simdref-web.png img/web.png
    git add img/*
    git commit -m "docs: refresh screenshots"
    git push origin docs-assets
    git switch -                        # back to your working branch
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path


OUT_DIR = Path("/tmp")


async def render_tui() -> Path:
    """Dump a Textual SVG screenshot with the TUI pre-loaded on a query."""
    from simdref.tui import SimdrefApp

    app = SimdrefApp(initial_query="_mm_add_ps")
    # Taller terminal so the intrinsic metadata panel + perf table fit.
    async with app.run_test(size=(132, 48)) as pilot:
        # Initial search + thread-backed detail render are async workers.
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause(0.3)
        # The VerticalScroll detail pane auto-scrolls to the last mount
        # by default; reset to the top so the metadata panel is visible.
        detail = app.query_one("#detail-scroll")
        detail.scroll_home(animate=False)
        await pilot.pause(0.1)
        out = app.save_screenshot(filename="simdref-tui.svg", path=str(OUT_DIR))
    return Path(out)


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _serve(directory: Path, port: int):
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port)],
        cwd=str(directory),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for the server to accept connections.
        for _ in range(40):
            with closing(socket.socket()) as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def render_web() -> Path | None:
    """Screenshot the web UI in headless Firefox.

    Requires a freshly exported ``web/`` tree (use ``isa web`` first).
    Firefox's headless screenshot fires immediately after load, so the
    page may capture before the async catalog fetch completes. Accept
    that and rerun if the result looks blank.
    """
    firefox = shutil.which("firefox")
    if not firefox:
        print("firefox not on PATH — skipping web screenshot", file=sys.stderr)
        return None
    with tempfile.TemporaryDirectory() as tmp_dir:
        web_dir = Path(tmp_dir) / "web"
        from simdref.storage import load_catalog
        from simdref.web import export_web

        export_web(load_catalog(), web_dir)
        port = _free_port()
        out = OUT_DIR / "simdref-web.png"
        with _serve(web_dir, port):
            # Warm cache fetches — Firefox screenshots too eagerly.
            import urllib.request

            for path in ("/", "/search-index.json.gz", "/filter_spec.json.gz"):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).read()
                except Exception:
                    pass
            subprocess.run(
                [
                    firefox,
                    "--headless",
                    "--window-size=1400,900",
                    f"--screenshot={out}",
                    f"http://127.0.0.1:{port}/#_mm_add_ps",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    return out if out.exists() else None


def main() -> int:
    tui_path = asyncio.run(render_tui())
    print(f"TUI screenshot: {tui_path}")
    web_path = render_web()
    if web_path:
        print(f"Web screenshot: {web_path}")
    else:
        print("Web screenshot skipped — see script header for how to capture manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
