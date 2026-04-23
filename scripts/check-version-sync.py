#!/usr/bin/env python3
"""Fail loudly if pyproject.toml, marketplace.json, and plugin.json disagree.

The project's single source of truth for the release version is
``pyproject.toml:[project].version``. The Claude Code plugin metadata
files (``.claude-plugin/marketplace.json`` and
``.claude-plugin/plugin.json``) must match — otherwise users installing
via ``/plugin marketplace add`` see a stale version string.

Usage::

    python scripts/check-version-sync.py          # verify
    python scripts/check-version-sync.py --fix    # rewrite plugin files to match pyproject
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"


def _pyproject_version() -> str:
    return tomllib.loads(PYPROJECT.read_text())["project"]["version"]


def _marketplace_version(data: dict) -> str:
    plugins = data.get("plugins") or []
    if not plugins:
        raise RuntimeError("marketplace.json has no plugins")
    return plugins[0].get("version", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="rewrite plugin files to match pyproject")
    args = parser.parse_args()

    canonical = _pyproject_version()
    marketplace = json.loads(MARKETPLACE.read_text())
    plugin = json.loads(PLUGIN.read_text())

    mv = _marketplace_version(marketplace)
    pv = plugin.get("version", "")

    mismatched: list[tuple[str, str, str]] = []
    if mv != canonical:
        mismatched.append((str(MARKETPLACE.relative_to(ROOT)), mv, canonical))
    if pv != canonical:
        mismatched.append((str(PLUGIN.relative_to(ROOT)), pv, canonical))

    if not mismatched:
        print(f"version sync OK: {canonical}")
        return 0

    if not args.fix:
        print(f"pyproject.toml version is {canonical!r}; out of sync:")
        for path, found, expected in mismatched:
            print(f"  {path}: {found!r} != {expected!r}")
        print("re-run with --fix to rewrite plugin metadata in place.")
        return 1

    # --fix: rewrite both files preserving key order and indentation.
    marketplace["plugins"][0]["version"] = canonical
    MARKETPLACE.write_text(json.dumps(marketplace, indent=2) + "\n")
    plugin["version"] = canonical
    PLUGIN.write_text(json.dumps(plugin, indent=2) + "\n")
    print(f"rewrote plugin metadata to version {canonical}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
