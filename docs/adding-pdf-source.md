# Adding a PDF Source

This refactor treats PDF enrichment as a source-pluggable subsystem under `simdref.pdfparse`.

## Required source spec fields

Create one module that defines and registers a `PdfSourceSpec`:

- `source_id`: stable internal id, also used in cache keys and `InstructionRecord.pdf_refs`
- `display_name`: human-facing label shown in exported metadata
- `source_url`: canonical upstream PDF URL
- `local_candidates`: preferred local/vendor cache paths
- `cache_path`: derived cache file for parsed descriptions
- `cache_version`: bump when the serialized result shape changes
- `signature_paths`: source files whose contents should invalidate the cache when edited
- `parser`: callable returning `PdfEnrichmentResult`
- `find_source`: callable that locates or downloads the PDF and returns a local path

## Parser responsibilities

The parser should return `PdfEnrichmentResult` with:

- `descriptions`: mnemonic -> `PdfDescriptionPayload`
- `fallback_page_count`: pages that needed a slower fallback path, if relevant
- `stats`: optional counters for status output and tests

Each `PdfDescriptionPayload` should include:

- `sections`: merged section text keyed by canonical section name
- `source_url`
- `page_start`
- `page_end`

The parser module should own all source-specific constants, heuristics, and fallback logic. Generic ingest code should not need to know about page-title patterns, section aliases, or parser internals.

## Cache invalidation

`ingest_pdf.load_or_parse_pdf_source()` invalidates cached results when any of these change:

- `cache_version`
- parser signature derived from `signature_paths`
- canonical `source_url`
- PDF file SHA-256

Use `cache_version` for serialized payload shape changes. Use `signature_paths` for parser behavior changes.

## Data model expectations

- Attach references through `InstructionRecord.pdf_refs`, not source-specific metadata keys.
- Keep parsed section text in `InstructionRecord.description`.
- If a source needs compatibility metadata during migration, add that in a shared helper rather than in UI code.

## Expected tests

- registry lookup returns the registered spec
- cache hit/miss behavior for parser signature or PDF checksum changes
- parser unit tests for source-specific extraction rules
- metadata normalization tests for `pdf_refs`
- UI/export tests showing normalized refs render without source-specific formatting logic in CLI/TUI/web
- integration path proving the source can participate in a local build

## CI validation path

- Keep GitHub Actions workflow logic generic
- Add source-specific smoke checks through a shared validation script or shared Python entrypoint
- Avoid embedding new source-specific inline workflow snippets when a reusable validation hook can cover them
