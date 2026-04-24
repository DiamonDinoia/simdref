# `simdref llm` — structured output for LLM / tool consumption

`isa llm` (aka `simdref llm`) is the subcommand group intended for
programmatic consumers: agents, skills, editor integrations, and ad-hoc
scripts that want to reason about SIMD intrinsics and instructions without
having to parse human-oriented TUI or Markdown output.

The CLI ships under two executable names: **`isa`** (short) and
**`simdref`** (explicit). Both run the same code, and every example
below works identically under either name — this document uses `isa` for
brevity.

Every subcommand emits stable JSON or NDJSON on stdout; all progress, errors,
and diagnostics go to stderr. That separation lets callers safely pipe
stdout into `jq` / `json.loads`.

## Subcommands

### `isa llm query QUERY...`

Resolve a single query (intrinsic name, instruction mnemonic, or free-form
search) and emit one payload.

| Flag                                   | Default | Description                                                                                                                    |
| -------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `--format, -F json\|ndjson\|markdown`  | `json`  | Pretty JSON, one-object-per-line NDJSON, or prompt-friendly Markdown.                                                          |
| `--limit N`                            | `8`     | Maximum number of search results when the query falls through to search mode.                                                  |
| `--isa FAM` (repeatable)               | all     | Filter by ISA family (`Intel`, `Arm`, `RISC-V`, …).                                                                            |
| `--preset NAME`                        | none    | Apply a named preset (`default`, `intel`, `arm32`, `arm64`, `riscv`, `none`, `all`).                                           |
| `--source-kind measured\|modeled\|any` | `any`   | Filter performance rows by provenance. `measured` keeps only uops.info-style rows; `modeled` keeps only llvm-mca–derived rows. |

**Payload shape** (abridged):

```json
{
  "query": "_mm_add_epi32",
  "mode": "exact",
  "match_kind": "intrinsic",
  "result": {
    "intrinsic": "_mm_add_epi32",
    "signature": "__m128i _mm_add_epi32(__m128i a, __m128i b)",
    "instructions": ["paddd"],
    "instruction_refs": [{ "key": "...", "name": "...", "form": "...", "architecture": "...", "xed": "...", "resolution": "...", "match_count": 1 }],
    "isa": ["SSE2"],
    "lat": "1",
    "cpi": "0.5",
    "summary": "Add packed 32-bit integers."
  }
}
```

Free-form search yields `{"mode": "search", "results": [...]}` where each
entry has the same shape as `result`.

### `isa llm batch`

Reads queries one-per-line from stdin and emits one NDJSON record per input
line, amortizing catalog load across hundreds of lookups. Blank lines and
lines starting with `#` are skipped.

```bash
echo -e "_mm_add_ps\nVPADDD\n_does_not_exist" | isa llm batch
```

Each output record has the shape:

```json
{"query": "_mm_add_ps", "status": "match", "payload": { ... }}
```

`status` is one of `match`, `no_match`, `ambiguous`, or `error` (the last
indicates an internal failure; the record will also carry an `error` field).

Accepts the same `--limit`, `--isa`, `--preset`, `--source-kind` flags as
`query`.

### `isa llm list`

Without arguments, emits the full `FilterSpec` describing ISA families,
sub-ISAs, and the category catalog:

```bash
isa llm list --format json
isa llm list --format markdown
```

With `--pattern GLOB [--isa FAM]`, streams NDJSON records of the form
`{name, kind, isa, category}` for each intrinsic/instruction whose name
matches the glob and (optionally) lives inside one of the requested ISA
families. The pattern uses `fnmatch` (shell globs — `*`, `?`, `[...]`) and
is applied case-insensitively to both the entry name and its `db_key`.

```bash
isa llm list --pattern "*gather*" --isa "Intel"
```

### `isa llm schema`

Emits a JSON Schema describing the `query` / `batch` payload shape, including
the `generated_at` timestamp, `source_versions` array, and nested
`instruction_refs` fields. Useful to generate client-side types.

## Exit codes

| Code | Meaning                                                              |
| ---- | -------------------------------------------------------------------- |
| `0`  | Match (intrinsic, instruction, or at least one search result).       |
| `1`  | Usage error — bad flag, unknown preset, missing argument.            |
| `2`  | Query valid but no catalog match.                                    |
| `3`  | Ambiguous: multiple exact instruction matches for the same mnemonic. |
| `10` | Internal error (exception during resolution).                        |

## Claude skill recipe

The `llm` interface is designed to support a Claude skill that reads a
user's assembly/codegen and suggests intrinsic-level optimizations. Typical
loop:

1. **Identify mnemonics.** Parse the assembly, extract instruction mnemonics.
1. **Pre-filter** the catalog for speed:
   ```bash
   isa llm list --pattern "VPADD*" --isa "Intel"
   ```
1. **Resolve each mnemonic** in one batch call, keeping measured perf only:
   ```bash
   printf '%s\n' "${mnemonics[@]}" | isa llm batch --source-kind measured
   ```
1. **Consume the NDJSON stream.** Each record contains `lat`, `cpi`, the
   linked intrinsic name, and the canonical `summary` — enough to propose
   a replacement intrinsic and cite the measured latency/throughput.
1. **Cite provenance.** The top-level `generated_at` and `source_versions`
   fields tell the skill when the advice was sourced from, so the skill
   can surface staleness warnings if the catalog is old.
