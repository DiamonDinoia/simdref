# simdref

A local SIMD reference workbench that combines Intel Intrinsics Guide data with
[uops.info](https://uops.info) instruction and performance measurements into a
single searchable catalog.

Interfaces: CLI with smart lookup, TUI, LSP hover + completion, generated
manpages, and a static web app.

## Install

```bash
pip install simdref
simdref update          # fetch Intel intrinsics + uops.info data
simdref doctor          # verify installation
```

Or from [TestPyPI](https://test.pypi.org/project/simdref/) (pre-release):

```bash
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ simdref
```

### Development

```bash
git clone https://github.com/MarcoBarbone/simdref.git
cd simdref
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/simdref update
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
| `simdref update [--offline]` | Rebuild catalog from upstream (or fixtures) |
| `simdref search <query>` | Ranked search results table |
| `simdref show intrinsic <name>` | Display a specific intrinsic |
| `simdref show instruction <name>` | Display a specific instruction |
| `simdref llm <query>` | Structured JSON output for LLM consumption |
| `simdref man <name>` | Open generated manpage |
| `simdref complete <prefix>` | List completion candidates |
| `simdref shell-init bash` | Print bash completion setup |
| `simdref tui` | Interactive terminal UI |
| `simdref export-web` | Generate static web app |
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
simdref export-web --web-dir ./web
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

The `update` command fetches from upstream CDNs with automatic fallback to
bundled fixtures when offline. Vendor archives can also be placed in `vendor/`.

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

MIT
