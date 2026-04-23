#!/usr/bin/env python3
"""Bump the release version across every file that carries it.

Usage::

    python scripts/bump-version.py 0.2.0

Edits:
- ``pyproject.toml``       [project].version
- ``.claude-plugin/marketplace.json``  plugins[0].version
- ``.claude-plugin/plugin.json``       .version

Commit the result yourself. The release-candidate workflow refuses to
cut a tag unless all three agree.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"


def _rewrite_pyproject(version: str) -> None:
    text = PYPROJECT.read_text()
    new_text, n = re.subn(
        r'^version\s*=\s*"[^"]*"',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise RuntimeError("could not locate version line in pyproject.toml")
    PYPROJECT.write_text(new_text)


def _rewrite_json(path: pathlib.Path, mutate) -> None:
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data, indent=2) + "\n")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} X.Y.Z", file=sys.stderr)
        return 2
    version = sys.argv[1].lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[.-][\w.]+)?", version):
        print(f"invalid version: {version!r}", file=sys.stderr)
        return 2

    _rewrite_pyproject(version)
    _rewrite_json(MARKETPLACE, lambda d: d["plugins"][0].__setitem__("version", version))
    _rewrite_json(PLUGIN, lambda d: d.__setitem__("version", version))

    print(f"bumped pyproject + .claude-plugin metadata to {version}")
    print("next: git commit -am 'chore(release): bump version to " f"{version}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
