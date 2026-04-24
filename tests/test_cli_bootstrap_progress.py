"""Bootstrap banner + download progress reporting (issue #5).

A fresh install's first ``simdref annotate`` (or any command) triggers
``_bootstrap_interactive`` which downloads ``catalog.msgpack`` and
``catalog.db`` from a GitHub release. On the user's machine that took
~10 minutes with no output, indistinguishable from a hang in CI / agent
harnesses.

These tests exercise the plumbing — they do not hit the network; they
stub ``httpx.stream`` and inspect what gets printed.
"""

from __future__ import annotations

import io
import unittest
from contextlib import contextmanager
from unittest import mock

import httpx


class _FakeResponse:
    def __init__(self, body: bytes, status_code: int = 200, content_length: bool = True):
        self._body = body
        self.status_code = status_code
        self.headers = {"content-length": str(len(body))} if content_length else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=mock.Mock(), response=mock.Mock(status_code=self.status_code)
            )

    def iter_bytes(self, chunk_size: int = 1024):
        # One chunk keeps the test deterministic; callers don't care.
        yield self._body


@contextmanager
def _stream_returning(body: bytes):
    @contextmanager
    def fake_stream(*args, **kwargs):
        yield _FakeResponse(body)
    with mock.patch("httpx.stream", fake_stream):
        yield


class DownloadProgressTests(unittest.TestCase):
    def test_non_tty_emits_per_asset_size_line(self):
        """In CI / non-TTY mode, each asset download should print a
        ``downloaded <asset>: N.N MB`` line so harnesses observe progress."""
        import tempfile
        from pathlib import Path
        from rich.console import Console
        from simdref import cli

        buf = io.StringIO()
        fake_console = Console(file=buf, force_terminal=False, width=200)

        tmp = Path(tempfile.mkdtemp())
        with _stream_returning(b"x" * (2 * 1024 * 1024)), \
             mock.patch.object(cli, "err_console", fake_console), \
             mock.patch.object(cli, "DATA_DIR", tmp):
            cli._download_from_release()

        output = buf.getvalue()
        self.assertIn("catalog.msgpack", output)
        self.assertIn("catalog.db", output)
        self.assertIn("downloaded", output)
        self.assertIn("MB", output)
        self.assertIn("download complete", output)

    def test_bootstrap_banner_includes_hint(self):
        """The bootstrap banner must mention ``simdref update`` so users
        know how to pre-fetch explicitly next time."""
        from simdref import cli

        captured_err = []
        captured_out = []

        def _err_print(*args, **kwargs):
            captured_err.append(" ".join(str(a) for a in args))

        def _typer_echo(msg, *args, **kwargs):
            captured_out.append(str(msg))

        with mock.patch.object(cli.err_console, "print", side_effect=_err_print), \
             mock.patch("simdref.cli.typer.echo", side_effect=_typer_echo), \
             mock.patch("sys.stdout.isatty", return_value=False), \
             mock.patch.object(cli, "_download_release_or_fallback"):
            cli._bootstrap_interactive()

        banner = "\n".join(captured_err)
        self.assertIn("bootstrap", banner.lower())
        self.assertIn("simdref update", banner)
        # Non-TTY stdout markers bracket the download so log scrapers can
        # detect the bootstrap window.
        stdout = "\n".join(captured_out)
        self.assertIn("bootstrapping catalog", stdout)
        self.assertIn("bootstrap complete", stdout)


if __name__ == "__main__":
    unittest.main()
