---
name: asm-analysis
description: |-
  Use when the user asks Codex to inspect, annotate, profile, or optimise
  generated assembly for C/C++ or raw .s/object/binary inputs. Triggers
  include "look at the asm", "inspect codegen", "why is this loop slow",
  "vectorise this", "make this faster", "tune this loop", "use AVX-512",
  "make this faster on Zen4/Skylake-X/Apple M2", or requests for
  intrinsic-level replacements with cited latency/CPI. Do not use for
  pure source-level refactoring, style changes, or C++ semantics questions
  that do not require generated code.
---

# asm-analysis

This is the Codex entrypoint for the shared simdref assembly analysis
workflow.

Before doing any assembly analysis, read and follow
`references/workflow.md`. Treat that file as the source of truth for the
compile → objdump/-S → simdref annotate → simdref llm batch → proposal
pipeline.

## Codex install and updates

Preferred (marketplace one-liner, from a Codex CLI prompt):

```
codex plugin marketplace add DiamonDinoia/simdref
/plugins
```

Pick `asm-analysis` and enable it.

Manual install — Codex discovers skills from `.agents/skills/` (repo)
or `~/.agents/skills/` (user). See the
[Codex skills docs](https://developers.openai.com/codex/skills).

```bash
git clone https://github.com/DiamonDinoia/simdref.git ~/src/simdref
mkdir -p ~/.agents/skills
ln -sf ~/src/simdref/codex-skills/asm-analysis/skills/asm-analysis \
       ~/.agents/skills/asm-analysis
```

Refresh by updating the checkout: `(cd ~/src/simdref && git pull)`.
