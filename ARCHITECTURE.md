# Architecture

## Module layout

```
src/simdref/
  models.py      Data classes: IntrinsicRecord, InstructionRecord, Catalog
  ingest.py      Stable public ingest entrypoints / compatibility wrappers
  ingest_sources.py  Source acquisition for Intel intrinsics + uops.info
  ingest_catalog.py  Parse, link, and assemble Catalog records
  ingest_pdf.py      PDF enrichment cache/load/merge dispatch
  storage.py     JSON and SQLite persistence, FTS5 search
  search.py      Fuzzy ranking with intent detection
  perf.py        Shared latency/throughput extraction helpers
  queries.py     Shared record-linking and lookup helpers
  display.py     Rich terminal formatting and rendering
  pdfrefs.py     Normalized PDF reference helpers shared by CLI/TUI/web
  cli.py         Typer commands and smart lookup dispatch
  lsp.py         JSON-RPC language server (hover + completion)
  tui.py         Curses-based interactive search
  manpages.py    Roff manpage generation
  web.py         Static web app export
  pdfparse/      Source-pluggable PDF enrichment implementations + registry
  templates/     HTML template for the web SPA
  fixtures/      Sample data for offline bootstrapping and tests
```

## Data flow

```
Intel CDN / uops.info / vendor archives / fixtures
                    |
                    v
          ingest_sources.py
         fetch + source versioning
                    |
                    v
          ingest_catalog.py
          parse + link + assemble
                    |
          optional ingest_pdf.py
      cache + dispatch to pdfparse registry
                    |
                    v
              Catalog
                    |
        +-----------+-----------+
        v                       v
  catalog.msgpack          catalog.db
  (portable snapshot)   (SQLite + FTS5)
        |                       |
        v                       v
    web.py               cli.py / lsp.py
  (static export)     (runtime queries)
```

## PDF enrichment

- `pdfparse.types.PdfSourceSpec` defines one source entry: id, display name, source URL, local candidates, cache metadata, and parser callback.
- `pdfparse.registry` is the only registration point. Adding a new PDF source should be limited to implementing one parser module and registering its `PdfSourceSpec`.
- `ingest_pdf.load_or_parse_pdf_source()` owns cache invalidation. Cache keys include source id, parser signature, source URL, and PDF SHA-256.
- `InstructionRecord.pdf_refs` is the normalized public shape consumed by CLI/TUI/web export. Each ref includes `source_id`, `label`, `url`, `page_start`, and `page_end`.
- Legacy Intel metadata keys remain readable and are still written for compatibility during the migration window.

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
