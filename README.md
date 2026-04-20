# simdref

A local SIMD reference workbench that combines Intel Intrinsics Guide data with
[uops.info](https://uops.info) instruction and performance measurements into a
single searchable catalog.

Interfaces: CLI with smart lookup, TUI, LSP hover + completion, generated
manpages, and a static web app.

[Web App](https://diamondinoia.github.io/simdref/) |
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
required. Users who rebuild locally with `simdref update --build-local`
need `llvm-mca` 18+ on PATH; see `docs/SOURCES.md` for the full source
map and the `source_kind` labelling scheme. Every rendered latency/CPI
is tagged `(measured, <core>)` or `(modeled, <core>)` so measured and
modeled data never get mixed up.

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
.venv/bin/simdref update --build-local
```

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

| Command | Description |
|---------|-------------|
| `simdref update` | Download pre-built compatible data from GitHub Releases, with local fallback |
| `simdref update --build-local` | Refresh local Arm JSON cache and rebuild catalog from upstream sources |
| `simdref update --offline` | Build the bundled fixture dataset locally |
| `simdref llm <query>` | Structured JSON output for LLM consumption |
| `simdref llm query <name>` | Strict lookup (exit 2 on no-match, with `--isa` / `--category` filters) |
| `simdref llm list` | Enumerate catalog entries (arch/ISA filtered) |
| `simdref llm schema` | Print JSON schema for `llm` output |
| `simdref shell-init bash` | Print bash completion setup |
| `simdref web` | Generate static web app |
| `simdref doctor` | Validate installation |

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

### LLM integration

`simdref llm <query>` returns structured JSON:
- Exact matches: full intrinsic/instruction payload with performance summary
- Fuzzy matches: ranked results with `(lat, cpi)` pairs

## Data sources

| Source | What | Catalog entries¹ |
|--------|------|------|
| Intel Intrinsics Guide | Function signatures, descriptions, ISA, categories | 7,146 intrinsics |
| uops.info | Instructions, operands, latency, throughput, port usage | 22,276 instructions |
| Arm ACLE intrinsics | NEON/SVE intrinsic signatures and descriptions | 10,791 intrinsics |
| Arm AARCHMRS (A64) | Base instruction forms and operand tables | live-only² |
| riscv-rvv-intrinsic-doc | RVV intrinsic signatures, semantics, deterministic instruction refs | 74,319 intrinsics |
| RISC-V unified-db + docs.riscv.org fallback | RVV instruction forms, ISA tags, Description/Operation sections | 2,868 instructions |

¹ Counts are from the current `--build-local` rebuild with the `vendor/` archives
committed to this repo. See [`docs/coverage/summary.json`](docs/coverage/summary.json)
for live parity against upstream and [`docs/SOURCES.md`](docs/SOURCES.md) for
license and refresh-cadence details.

² Offline snapshots use the bundled fixture (2 sample instructions); the full
AARCHMRS spec is only available via live fetch or by placing the tarball under
`vendor/arm/`.

The default `update` path downloads pre-built data from GitHub Releases using
schema/version-compatible tags when available, with bundled fixtures as the
safe fallback. `simdref update --build-local` refreshes the vendored Arm
intrinsics JSON cache and then performs a full local rebuild, falling back to
the cached local vendor files if the refresh fails. Arm instruction imports can
also read a vendored `vendor/arm/a64_instructions.json` or an
`AARCHMRS_BSD*.tar.gz` archive placed under `vendor/arm/`. Bundled fixtures
remain the offline fallback.

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
