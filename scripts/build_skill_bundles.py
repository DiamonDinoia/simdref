"""Stamp the canonical `skill/` tree into per-agent skill directories.

Single source of truth: the `skill/` directory at the repo root.
Per-agent SKILL.md is built by concatenating
`frontmatter/<agent>.yaml` + body + `install/<agent>.md`. References
and agent-specific assets are copied verbatim. The output trees stay
committed so plugin install URLs keep resolving.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "skill"

GENERATED_HEADER = "<!-- generated from skill/ — do not edit -->\n"

TARGETS = {
    "claude": {
        "out": REPO / "skills" / "asm-analysis",
        "extras": [],
    },
    "codex": {
        "out": REPO / "codex-skills" / "asm-analysis" / "skills" / "asm-analysis",
        "extras": [
            (SRC / "agents" / "openai.yaml", "agents/openai.yaml"),
        ],
    },
}


def render_skill_md(agent: str) -> str:
    frontmatter = (SRC / "frontmatter" / f"{agent}.yaml").read_text()
    body = (SRC / "SKILL.md").read_text()
    install = (SRC / "install" / f"{agent}.md").read_text()
    if not frontmatter.endswith("\n"):
        frontmatter += "\n"
    if not body.endswith("\n"):
        body += "\n"
    parts = ["---\n", frontmatter, "---\n\n", GENERATED_HEADER, "\n", body, "\n", install]
    return "".join(parts)


def stamp(agent: str, out_root: Path, extras: list[tuple[Path, str]]) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "SKILL.md").write_text(render_skill_md(agent))

    refs_src = SRC / "references"
    refs_dst = out_root / "references"
    if refs_dst.exists():
        shutil.rmtree(refs_dst)
    shutil.copytree(refs_src, refs_dst)

    for src, rel in extras:
        dst = out_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def build_all(root: Path | None = None) -> None:
    for agent, cfg in TARGETS.items():
        out = cfg["out"] if root is None else root / agent
        stamp(agent, out, cfg["extras"])


def diff_trees(left: Path, right: Path) -> list[str]:
    drift: list[str] = []

    def walk(a: Path, b: Path, rel: str = "") -> None:
        cmp = filecmp.dircmp(a, b)
        for name in cmp.left_only:
            drift.append(f"only in canonical build: {rel}/{name}")
        for name in cmp.right_only:
            drift.append(f"only in committed tree: {rel}/{name}")
        for name in cmp.diff_files:
            drift.append(f"differs: {rel}/{name}")
        for name in cmp.common_dirs:
            walk(a / name, b / name, f"{rel}/{name}")

    walk(left, right)
    return drift


def check_all() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for agent, cfg in TARGETS.items():
            staging = tmp_root / agent
            stamp(agent, staging, cfg["extras"])
            drift = diff_trees(staging, cfg["out"])
            if drift:
                print(f"[{agent}] drift detected:", file=sys.stderr)
                for line in drift:
                    print(f"  {line}", file=sys.stderr)
                return 1
    print("OK: per-agent trees are in sync with skill/")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed per-agent trees match a fresh build; exit non-zero on drift.",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check_all()
    build_all()
    print("stamped per-agent skill bundles from skill/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
