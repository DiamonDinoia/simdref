# Changelog

All notable changes to `simdref` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## [0.0.4] — 2026-06-05

- **deps:** declare `click` as a direct dependency so a clean install can run
  the CLI without relying on it being pulled in transitively.
- **packaging:** derive `__version__` from installed package metadata, ending
  the drift between the module version and `pyproject.toml`.
- **release:** every release now publishes a versioned `data-v<version>` build
  automatically — the release workflow dispatches the data build on the freshly
  pushed tag, since release events created with `GITHUB_TOKEN` don't cascade.
- **asm-analysis skill:** added paired-interleaved benchmark, codegen-audit, and
  memory-traffic gates to the workflow; trimmed the skill description under the
  1024-char frontmatter cap without changing what triggers it.
- **web:** split the search index into three shards and load intrinsics lazily;
  extend kind/family buckets when intrinsics arrive in Phase 2.
- **catalog:** auto-update on version change; capture Intel Operation pseudocode
  and source URL.

## [0.0.3] — 2026-04-29

- **`simdref annotate`** — annotate a `.s` assembly file with per-instruction
  summaries and latency/CPI figures, emitting a `.sa` file that still
  assembles. `--performance` and `--docs` are on by default; `--arch` pins
  output to a specific microarch, otherwise aggregates across measured
  archs (`--agg avg|median|best|worst`).
- **ci:** fix runtime-validation step that still checked for the removed
  monolithic `web/intrinsic-details.json`; it now validates the per-prefix
  chunks under `web/intrinsic-chunks/`.
- **TUI:** respect kind toggles inside `_fts_search`; show no-sub-family
  instructions and bias ranking by query kind.

## [0.0.1] — 2026-04-28

- **`simdref profile`** documented alongside the bare-query CLI form in the
  README.
- **Codex plugin** marketplace and `asm-analysis` skill added, single-sourced
  from the canonical `skill/` tree via `scripts/build_skill_bundles.py`.
- **TUI:** ISA presets (`intel`/`arm32`/`arm64`/`riscv`) now include
  instructions, not just intrinsics.
- **CLI:** report progress during first-run catalog bootstrap; bare-query
  dispatch resolves `--arch` and skips the TUI on an exact match.
- **packaging:** drop the stray `simdref-build-skills` console script.

## [0.0.0] — 2026-04-22 — initial public release

First tagged release. The baseline set of interfaces the project aims to
support is in place:

- **CLI** with smart bare-word lookup (`simdref _mm_add_ps`, `simdref VPADDD`,
  fuzzy multi-token queries), grouped `--help` output (Commands / Dev commands),
  and stable exit codes. Also installed under the `isa` alias — both
  executables accept every subcommand.
- **`simdref build`** — full local rebuild from upstream sources
  (including Intel SDM parsing); replaces the old `simdref update --build` flag.
- **`simdref completion install|show`** subcommands for shell completion,
  replacing Typer's auto-generated `--install-completion` / `--show-completion`
  top-level options.
- **TUI** (Textual-based) browser with ISA/kind filters, presets, detail
  pane, `/` `?` `j/k` `1-9` `c` keybindings, and a help modal.
- **LSP** (`simdref-lsp`) providing hover + completion over JSON-RPC/stdio.
- **Manpages** generated per intrinsic / instruction.
- **Static web app** (`simdref web` / `simdref serve`) — a gzip-aware,
  self-contained SPA publishable to GitHub Pages, with build-stamp
  metadata for staleness warnings.
- **`simdref llm` JSON interface** for LLM / tool consumption:
  `query`, `batch` (stdin-driven, amortized catalog load), `list` (with
  optional `--pattern GLOB --isa FAM`), `schema`. See `docs/LLM.md`.
- **Source coverage**: Intel Intrinsics Guide, uops.info, Arm ACLE
  intrinsics + AARCHMRS A64, RISC-V `riscv-rvv-intrinsic-doc` and
  `riscv-unified-db`, with `docs.riscv.org` HTML fallback. Every perf row
  is tagged `measured` or `modeled` so the two never mix.

[0.0.0]: https://github.com/DiamonDinoia/simdref/releases/tag/v0.0.0
[0.0.1]: https://github.com/DiamonDinoia/simdref/releases/tag/v0.0.1
[0.0.3]: https://github.com/DiamonDinoia/simdref/releases/tag/v0.0.3
