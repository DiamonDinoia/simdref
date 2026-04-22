# Changelog

All notable changes to `simdref` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.0] — 2026-04-22 — initial public release

First tagged release. The baseline set of interfaces the project aims to
support is in place:

- **CLI** with smart bare-word lookup (`simdref _mm_add_ps`, `simdref VPADDD`,
  fuzzy multi-token queries), grouped `--help` output (Usage / Maintenance),
  and stable exit codes.
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
