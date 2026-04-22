# simdref

[![CI](https://github.com/DiamonDinoia/simdref/actions/workflows/ci.yml/badge.svg)](https://github.com/DiamonDinoia/simdref/actions/workflows/ci.yml)
[![Pages](https://github.com/DiamonDinoia/simdref/actions/workflows/pages.yml/badge.svg)](https://github.com/DiamonDinoia/simdref/actions/workflows/pages.yml)
[![PyPI](https://img.shields.io/pypi/v/simdref.svg)](https://pypi.org/project/simdref/)
[![Python](https://img.shields.io/pypi/pyversions/simdref.svg)](https://pypi.org/project/simdref/)

A single searchable reference for SIMD intrinsics and instructions across
**x86 (Intel + uops.info)**, **Arm (ACLE / AARCHMRS)**, and **RISC-V
(RVV + unified-db)**. Runs as a CLI, a Textual TUI, an LSP server,
generated manpages, a static web app, and a structured JSON interface
for LLM skills.

[Web App](https://diamondinoia.github.io/simdref/) ·
[PyPI](https://pypi.org/project/simdref/) ·
[TestPyPI](https://test.pypi.org/project/simdref/) ·
[GitHub](https://github.com/DiamonDinoia/simdref) ·
[Contributing](CONTRIBUTING.md)

<!-- Screenshots are hosted on the `docs-assets` branch so the main
     branch stays lightweight to clone. -->
<p align="center">
  <img alt="simdref TUI" src="https://raw.githubusercontent.com/DiamonDinoia/simdref/docs-assets/img/tui.svg" width="720">
  <br><em>Interactive TUI with ISA filters, ranked results, and measured/modeled performance tables.</em>
</p>

## Install

```bash
pip install simdref
isa update     # download the pre-built catalog
isa doctor     # confirm everything is wired up
isa            # open the TUI
```

The package installs two equivalent executables, **`isa`** (short) and
**`simdref`** (explicit). The rest of this README uses `isa`.

`isa update` pulls the combined catalog (x86 measured + Arm/RISC-V
measured & modeled) from the latest GitHub Release — **no `llvm-mca`
required**. Only contributors doing a full local rebuild with
`isa build` need `llvm-mca` 18+ on `PATH`.

Pre-release builds live on TestPyPI:

```bash
pip install -i https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ simdref
```

## Quickstart

```bash
isa _mm_add_ps       # exact intrinsic  -> detailed view
isa VPADDD           # exact instruction -> detailed view
isa _mm_add          # fuzzy -> ranked search results
isa mm add           # tokenized query -> intrinsic-biased search
isa ADD              # mnemonic-like -> instruction-biased search
isa VADDPS 2         # pick variant #2 from the last result list
isa                  # open the interactive TUI
```

## Interfaces

**Web app** — a self-contained static SPA with filters and performance
tables, published to GitHub Pages at
[diamondinoia.github.io/simdref](https://diamondinoia.github.io/simdref/).
Export your own copy:

```bash
isa web --web-dir ./web
isa serve --web-dir ./web       # gzip-aware local server
```

The [live demo](https://diamondinoia.github.io/simdref/) hosts the same
build — search across ~122k entries with ISA filters and per-uarch perf
tables.

**LSP** — hover docs + completion for intrinsic names and instruction
mnemonics in any LSP-capable editor:

```bash
simdref-lsp                      # speaks JSON-RPC over stdio
```

```lua
-- Neovim
vim.lsp.start({ name = "simdref", cmd = { ".venv/bin/simdref-lsp" } })
```

**LLM interface** — stable JSON / NDJSON for agents and editor skills,
with meaningful exit codes so tools can distinguish *no match* (2),
*ambiguous* (3), and *bad flag* (1):

```bash
isa llm query _mm_add_ps --source-kind measured
echo -e "_mm_add_ps\nVPADDD" | isa llm batch
isa llm list --pattern "*gather*" --isa Intel
```

See [docs/LLM.md](docs/LLM.md) for the full payload shape and a
Claude-skill recipe.

## Commands

`isa --help` groups commands into **Commands** (day-to-day) and
**Dev commands** (rebuild / export / completion).

**Commands**

| Command | Description |
|---------|-------------|
| `isa` / `isa <query>` | Open the TUI, pre-filling the query when one is given |
| `isa doctor` | Check the installation — pass/fail per component, non-zero exit on failure |
| `isa update` | Download the pre-built release catalog (no `llvm-mca` required) |
| `isa llm query <q>` | Strict lookup → JSON/NDJSON/Markdown (see [docs/LLM.md](docs/LLM.md)) |
| `isa llm batch` | Resolve many queries from stdin in one invocation (NDJSON out) |
| `isa llm list` | Dump the `FilterSpec` or stream matching catalog entries |
| `isa llm schema` | Print the JSON schema for `llm` payloads |

**Dev commands**

| Command | Description |
|---------|-------------|
| `isa build` | Full local rebuild from upstream sources (`llvm-mca` 18+ required) |
| `isa build --with-sdm` | Heaviest rebuild, also parses the Intel SDM PDF (CI / release) |
| `isa web` | Export the static web app under `web/` |
| `isa serve` | Serve the exported web app locally (gzip-aware) |
| `isa completion install [SHELL]` | Install shell completion into the user's profile |
| `isa completion show [SHELL]` | Print the completion script for a shell |

## Data sources

| Source | What | Entries¹ |
|--------|------|----------|
| Intel Intrinsics Guide | Signatures, descriptions, ISA, categories | 7,146 intrinsics |
| uops.info | Instructions, operands, latency, throughput, ports | 22,276 instructions |
| Arm ACLE (NEON/SVE) | Intrinsic signatures and descriptions | 10,791 intrinsics |
| Arm AARCHMRS (A64) | Base instruction forms and operand tables | live-only² |
| riscv-rvv-intrinsic-doc | RVV intrinsics with deterministic instruction refs | 74,319 intrinsics |
| RISC-V unified-db | RVV instruction forms, ISA tags, Description/Operation | 2,868 instructions |

¹ Counts from the current vendored snapshot. See
[`docs/coverage/summary.json`](docs/coverage/summary.json) for live
parity against upstream and [`docs/SOURCES.md`](docs/SOURCES.md) for
licenses and refresh cadence.

² The full AARCHMRS A64 spec is only available via live fetch or by
dropping the tarball under `vendor/arm/`.

Every rendered latency / CPI is tagged `(measured, <core>)` or
`(modeled, <core>)` so measured and modeled numbers never get silently
mixed.

### Scope caveats

- Performance data is x86-only in v1.
- RISC-V coverage is RVV-focused — not full scalar or privileged ISA.

## Development

```bash
git clone https://github.com/DiamonDinoia/simdref.git
cd simdref
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/isa build          # requires llvm-mca 18+
.venv/bin/python -m pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full dev flow
(tests, adding a new source, build stages) and
[ARCHITECTURE.md](ARCHITECTURE.md) for module layout.

## Dependencies

[httpx](https://www.python-httpx.org/) ·
[rapidfuzz](https://github.com/maxbachmann/RapidFuzz) ·
[rich](https://github.com/Textualize/rich) ·
[typer](https://typer.tiangolo.com/) ·
[textual](https://github.com/Textualize/textual)

## License

[GNU General Public License v3.0](LICENSE).
