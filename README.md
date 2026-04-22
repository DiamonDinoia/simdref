# simdref

[![CI](https://github.com/DiamonDinoia/simdref/actions/workflows/ci.yml/badge.svg)](https://github.com/DiamonDinoia/simdref/actions/workflows/ci.yml)
[![Pages](https://github.com/DiamonDinoia/simdref/actions/workflows/pages.yml/badge.svg)](https://github.com/DiamonDinoia/simdref/actions/workflows/pages.yml)
[![PyPI](https://img.shields.io/pypi/v/simdref.svg)](https://pypi.org/project/simdref/)
[![Python](https://img.shields.io/pypi/pyversions/simdref.svg)](https://pypi.org/project/simdref/)

A local SIMD reference workbench that combines Intel Intrinsics Guide data with
[uops.info](https://uops.info) instruction and performance measurements, the
Arm ACLE / AARCHMRS sources, and the RISC-V RVV catalog into a single
searchable reference.

Interfaces: CLI with smart lookup, TUI, LSP hover + completion, generated
manpages, a static web app, and a structured [`simdref llm` JSON
interface](docs/LLM.md) designed for LLM / Claude-skill consumption.

[Web App](https://diamondinoia.github.io/simdref/) |
[PyPI](https://pypi.org/project/simdref/) |
[TestPyPI](https://test.pypi.org/project/simdref/) |
[GitHub](https://github.com/DiamonDinoia/simdref)

## Install

```bash
pip install simdref
simdref update          # download the pre-built GitHub release catalog
simdref doctor          # verify installation
```

The default `simdref update` downloads the combined derived catalog
(x86 measured + ARM/RISC-V measured & modeled) — `llvm-mca` is **not**
required. Users who rebuild locally with `simdref update --build` need
`llvm-mca` 18+ on PATH; see `docs/SOURCES.md` for the full source map and
the `source_kind` labelling scheme. Every rendered latency/CPI is tagged
`(measured, <core>)` or `(modeled, <core>)` so measured and modeled data
never get mixed up.

Or from [TestPyPI](https://test.pypi.org/project/simdref/) (pre-release):

```bash
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ simdref
```

### Development

```bash
git clone https://github.com/DiamonDinoia/simdref.git
cd simdref
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/simdref update --build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full dev flow (tests,
adding a new source, how the build stages fit together).

## Usage

### Smart lookup (bare words)

```bash
simdref _mm_add_ps          # exact intrinsic -> detailed view
simdref VPADDD               # exact instruction -> detailed view
simdref _mm_add              # fuzzy -> ranked search results
simdref mm add               # tokenized query -> intrinsic-biased search
simdref ADD                  # mnemonic-like -> instruction-biased search
simdref VADDPS 2             # pick variant #2 from the list
```

### Subcommands

`simdref --help` groups commands into **Usage** (day-to-day) and
**Maintenance** (rebuild / export). The quick reference:

**Usage**

| Command | Description |
|---------|-------------|
| `simdref` (bare) or `simdref <query>` | Open the TUI, pre-filling the query when one is given |
| `simdref serve` | Serve the exported static web app locally (gzip-aware) |
| `simdref doctor` | Validate installation and show catalog stats |
| `simdref update` | Refresh runtime data (download pre-built release catalog by default) |
| `simdref llm query <query>` | Strict lookup → JSON/NDJSON/Markdown; exit 2 on no-match (see [docs/LLM.md](docs/LLM.md)) |
| `simdref llm batch` | Resolve many queries from stdin in one invocation (NDJSON out) |
| `simdref llm list [--pattern GLOB --isa FAM]` | Dump the `FilterSpec` or stream matching catalog entries |
| `simdref llm schema` | Print JSON schema for `llm` payloads |

**Maintenance**

| Command | Description |
|---------|-------------|
| `simdref web` | Export the static web app under `web/` |
| `simdref update --build` | Full local rebuild from upstream sources (requires `llvm-mca` on PATH; advanced / CI use) |
| `simdref update --build --with-sdm` | Heaviest local rebuild, also parses the Intel SDM PDF (CI / release generation) |

### LSP

```bash
simdref-lsp                  # speaks JSON-RPC over stdio
```

Provides hover documentation and completion for intrinsic names and instruction
mnemonics. Works in any editor that supports LSP.

Neovim:
```lua
vim.lsp.start({
  name = "simdref",
  cmd = { ".venv/bin/simdref-lsp" },
})
```

### Web app

```bash
simdref web --web-dir ./web
python3 -m http.server -d ./web 8000
# open http://localhost:8000
```

Self-contained static SPA with search, ISA filtering, and performance tables.
Publishable directly to GitHub Pages.

### LLM interface

`simdref llm` is the subcommand group intended for agents, editor skills,
and other programmatic consumers — it emits stable JSON / NDJSON on stdout
and keeps exit codes meaningful so tools can tell "no match" (exit 2) apart
from "bad flag" (exit 1) and "ambiguous" (exit 3).

Typical calls:

```bash
simdref llm query _mm_add_ps --source-kind measured
echo -e "_mm_add_ps\nVPADDD" | simdref llm batch
simdref llm list --pattern "*gather*" --isa Intel
```

See [docs/LLM.md](docs/LLM.md) for the full payload shape, exit-code
table, and a Claude-skill recipe.

## Data sources

| Source | What | Catalog entries¹ |
|--------|------|------|
| Intel Intrinsics Guide | Function signatures, descriptions, ISA, categories | 7,146 intrinsics |
| uops.info | Instructions, operands, latency, throughput, port usage | 22,276 instructions |
| Arm ACLE intrinsics | NEON/SVE intrinsic signatures and descriptions | 10,791 intrinsics |
| Arm AARCHMRS (A64) | Base instruction forms and operand tables | live-only² |
| riscv-rvv-intrinsic-doc | RVV intrinsic signatures, semantics, deterministic instruction refs | 74,319 intrinsics |
| RISC-V unified-db + docs.riscv.org fallback | RVV instruction forms, ISA tags, Description/Operation sections | 2,868 instructions |

¹ Counts are from the current `simdref update --build` rebuild with the `vendor/` archives
committed to this repo. See [`docs/coverage/summary.json`](docs/coverage/summary.json)
for live parity against upstream and [`docs/SOURCES.md`](docs/SOURCES.md) for
license and refresh-cadence details.

² The full AARCHMRS A64 spec is only available via live fetch or by placing
the tarball under `vendor/arm/`.

The default `update` path downloads pre-built data from GitHub Releases using
schema/version-compatible tags when available. `simdref update --build`
refreshes the vendored Arm intrinsics JSON cache and then performs a full
local rebuild, falling back to the cached local vendor files if the refresh
fails. Arm instruction imports can also read a vendored
`vendor/arm/a64_instructions.json` or an `AARCHMRS_BSD*.tar.gz` archive placed
under `vendor/arm/`.

### RISC-V status

Official-first ingest, full upstream coverage against the vendored snapshot:

- RVV intrinsics come from `riscv-rvv-intrinsic-doc` (74,319 entries) with
  stable project-level URLs instead of synthetic per-intrinsic anchors.
- Instructions come from `riscv-unified-db` (2,868 entries) and keep dotted
  mnemonic forms plus extension/policy metadata.
- Missing instruction semantics are enriched from `docs.riscv.org` HTML
  fallback when available.

Known exclusions:

- Performance data is still x86-only in v1.
- RISC-V coverage is broader RVV, not full scalar or privileged ISA completeness.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for module layout and data flow.

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

## Dependencies

- [httpx](https://www.python-httpx.org/) -- HTTP client for upstream fetches
- [rapidfuzz](https://github.com/maxbachmann/RapidFuzz) -- fuzzy string matching (falls back to difflib)
- [rich](https://github.com/Textualize/rich) -- terminal formatting
- [typer](https://typer.tiangolo.com/) -- CLI framework

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
