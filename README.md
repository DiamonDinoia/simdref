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
simdref update          # download pre-built data; falls back to fixtures if needed
simdref doctor          # verify installation
```

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
| `simdref update` | Download pre-built compatible data, with fixture fallback |
| `simdref update --build-local` | Rebuild catalog locally from upstream sources |
| `simdref update --offline` | Build the bundled fixture dataset locally |
| `simdref llm <query>` | Structured JSON output for LLM consumption |
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

| Source | What | Size |
|--------|------|------|
| Intel Intrinsics Guide | Function signatures, descriptions, ISA, categories | ~4K intrinsics |
| uops.info | Instructions, operands, latency, throughput, port usage | ~3K instructions |

The default `update` path downloads pre-built data from GitHub Releases using
schema/version-compatible tags when available, with bundled fixtures as the
safe fallback. Use `simdref update --build-local` for a full local rebuild.
Vendor archives can also be placed in `vendor/`.

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
