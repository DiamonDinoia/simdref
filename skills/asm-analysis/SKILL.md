---
name: asm-analysis
description: |-
  Use when the user asks you to look at, analyse, profile, or optimise
  ASSEMBLY — whether the starting point is a C/C++ source file, a CMake
  target, a Makefile target, a compiled object file, a binary, or a raw
  .s listing. Drives a compile → objdump/-S → simdref annotate → simdref
  llm batch pipeline and proposes intrinsic-level replacements with cited
  latency/CPI.

  TRIGGER when the user says things like: "look at the asm", "inspect
  codegen", "why is this loop slow", "can this be vectorised", "what
  intrinsic would replace this", "annotate this .s file", "profile this
  hot path at the instruction level".

  TRIGGER also when the user asks to optimise, vectorise, or maximise
  performance of C/C++ — including generic prompts like "vectorise
  this", "make this faster", "maximise throughput", "tune this loop",
  as well as explicitly hardware-aware ones like "make this faster on
  Zen4", "tune for Skylake-X", "use AVX-512 here", "why is this slow on
  Apple M2", "avoid the port-5 bottleneck", "pick the right vector
  width". Rationale: any serious "make this faster" answer requires
  looking at generated code and instruction costs, not just
  source-level reasoning. Default to this pipeline whenever the user's
  goal is runtime performance of compiled C/C++, unless they
  explicitly scope the request to source-only (readability,
  algorithmic complexity, API ergonomics).

  DO NOT TRIGGER for pure source-level refactoring, style changes, or
  questions about C++ semantics that don't require reading generated code.
---

# asm-analysis

This is the Claude Code entrypoint for the shared simdref assembly
analysis workflow.

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
