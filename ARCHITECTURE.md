# Architecture

## Module layout

```
src/simdref/
  models.py      Data classes: IntrinsicRecord, InstructionRecord, Catalog
  ingest.py      Fetch, parse, and link data from Intel + uops.info
  storage.py     JSON and SQLite persistence, FTS5 search
  search.py      Fuzzy ranking with intent detection
  perf.py        Shared latency/throughput extraction helpers
  queries.py     Shared record-linking and lookup helpers
  display.py     Rich terminal formatting and rendering
  cli.py         Typer commands and smart lookup dispatch
  lsp.py         JSON-RPC language server (hover + completion)
  tui.py         Curses-based interactive search
  manpages.py    Roff manpage generation
  web.py         Static web app export
  templates/     HTML template for the web SPA
  fixtures/      Sample data for offline bootstrapping and tests
```

## Data flow

```
Intel CDN / uops.info / vendor archives / fixtures
                    |
                    v
              ingest.py
         fetch + parse + link
                    |
        +-----------+-----------+
        v                       v
  IntrinsicRecord[]      InstructionRecord[]
        |                       |
        +--- link_records() ----+
        |  (bidirectional refs) |
        v                       v
              Catalog
                    |
        +-----------+-----------+
        v                       v
  catalog.json            catalog.db
  (JSON backup)        (SQLite + FTS5)
        |                       |
        v                       v
    web.py               cli.py / lsp.py
  (static export)     (runtime queries)
```

## Storage strategy

- **JSON** (`catalog.json`): complete serialised catalog for portability and
  offline use. Loaded once for `export-web`, `tui`, `man`, and `doctor`.
- **SQLite** (`catalog.db`): FTS5 full-text search with BM25 ranking for fast
  CLI `search`, `show`, `complete`, and `llm` queries. Schema is versioned;
  rebuilt automatically when stale.

## Search algorithm

The search pipeline in `search.py` scores candidates through multiple factors:

1. **Intent detection** -- queries starting with `_mm` bias towards intrinsics;
   mnemonic-like queries (`add`, `vmov`) bias towards instructions.
2. **Exact/prefix/substring matching** -- strong bonuses (220/175/135 points).
3. **Normalised token matching** -- splits on `_`, `,`, `{}` and compares tokens.
4. **Fuzzy matching** -- rapidfuzz `token_set_ratio`, `partial_ratio`, `ratio`.
5. **Width family bonus** -- +22 for matching SIMD width (`mm256`, `ymm`).
6. **Score threshold** -- results below 35 points are discarded.

For runtime search, FTS5 provides the candidate set (up to 12x the limit) and
the scoring pipeline re-ranks them.

## ISA filtering

Instruction variants are sorted chronologically by ISA generation. APX and
FP16/BF16 variants are hidden by default (pass `--fp16` to show them).
