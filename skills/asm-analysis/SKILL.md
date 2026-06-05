---
name: asm-analysis
description: |-
  Use when the user asks to look at, analyse, profile, or optimise
  ASSEMBLY or the runtime performance of compiled C/C++ — from a source
  file, CMake/Makefile target, object file, binary, or raw .s listing.

  TRIGGER on: "look at the asm", "inspect codegen", "why is this loop
  slow", "can this be vectorised", "what intrinsic replaces this",
  "annotate this .s", and on any "make this faster / vectorise / tune /
  maximise throughput" request for C/C++, including hardware-specific
  ones ("faster on Zen4", "tune for Skylake-X", "use AVX-512", "avoid
  the port-5 bottleneck"). Default here whenever the goal is runtime
  performance of compiled C/C++, unless scoped to source-only
  (readability, algorithmic complexity, API ergonomics).

  DO NOT TRIGGER for pure source refactoring, style, or C++ semantics
  questions that don't require reading generated code.
---

<!-- generated from skill/ — do not edit -->

# asm-analysis

Entrypoint for the shared simdref assembly analysis workflow.

Before doing any assembly analysis, read and follow
`references/workflow.md`. Treat that file as the source of truth for the
compile → objdump/-S → simdref annotate → simdref llm batch → proposal
pipeline.

## Claude Code install and updates

This skill lives at `skills/asm-analysis/SKILL.md` inside the simdref
source tree, which is also published as a Claude Code plugin
marketplace. Preferred install at the Claude Code prompt:

```
/plugin marketplace add DiamonDinoia/simdref
/plugin install asm-analysis@simdref
```

To refresh later:

```
/plugin marketplace update simdref
/plugin install asm-analysis@simdref
```

Manual alternatives, either symlink from a checkout for always-current
updates or install a one-off snapshot:

```bash
git clone https://github.com/DiamonDinoia/simdref.git ~/src/simdref
mkdir -p ~/.claude/skills
ln -sf ~/src/simdref/skills/asm-analysis ~/.claude/skills/asm-analysis
# later: (cd ~/src/simdref && git pull)  # updates skill in place

# or snapshot:
mkdir -p ~/.claude/skills/asm-analysis && \
  curl -fsSL https://raw.githubusercontent.com/DiamonDinoia/simdref/main/skills/asm-analysis/SKILL.md \
  -o ~/.claude/skills/asm-analysis/SKILL.md
```

If the user has an older PyPI-shipped copy, point them at the packaged
location for the upgrade diff:

```bash
pip show -f simdref | grep SKILL.md
```
