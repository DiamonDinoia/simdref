# PDF Description Parsing Design

**Date:** 2026-04-10

## Goal

Add rich instruction descriptions (Description, Operation pseudocode, Exceptions, Intrinsic Equivalents) parsed from the Intel SDM PDF to simdref. Display them in an expandable/scrollable pager view in the CLI and as collapsible sections in the web UI. Support a `--short`/`-s` flag to skip descriptions.

## Architecture

Parse the Intel SDM PDF with pdfplumber during `simdref update`. Extract per-instruction sections by detecting font-size-based headings. Store extracted sections as a `dict[str, str]` on `InstructionRecord.description`. The raw PDF is never redistributed; only the post-processed catalog ships via GitHub Releases.

The parsing module (`src/simdref/pdfparse/`) is designed to be ISA-agnostic at the base layer, with ISA-specific subclasses (starting with Intel, extensible to ARM/RISC-V).

## Data Model Change

`InstructionRecord` gains a `description` field:

```python
@dataclass(slots=True)
class InstructionRecord:
    # ... existing fields ...
    description: dict[str, str] = field(default_factory=dict)
    # Keys: "Description", "Operation", "Flags Affected",
    #        "Exceptions", "Intrinsic Equivalents"
```

The existing `summary` field (one-liner) is unchanged and used for search results / `--short` mode.

## PDF Parsing Pipeline

### Intel SDM Structure (verified via pdfplumber analysis)

- **5342 pages total**, instruction reference in chapters 3-6 of Volume 2 (~pages 700-2900)
- Each instruction starts on a new page with a **size-12 font title** containing `—` (em-dash)
  - Example: `ADDPS—Add Packed Single Precision Floating-Point Values`
- **Section headers** are **size-10 font** (`NeoSansIntelMedium`):
  `Description`, `Operation`, `Intel C/C++ Compiler Intrinsic Equivalent`, `Exceptions`, `Numeric Exceptions`, `Other Exceptions`
- **Body text** is `Verdana:9.0`
- Instructions span 1-6 pages; next instruction's title marks the boundary
- Footer line matches pattern: `MNEMONIC—Summary Vol. 2X N-NN`

### Parsing Strategy

1. **Single pass** over instruction reference pages (~686-2900): detect title pages by font size >= 12 + regex `^[A-Z][A-Z0-9/\s]{1,60}—.+` (all-caps mnemonic before em-dash). Filter out chapter/section headings.
2. **Group pages** into instruction ranges: `[title_page_i, title_page_i+1)`
3. **Extract sections** within each range by detecting size-10 headings
4. **Accumulate body text** (Verdana:9.0) under each heading, stripping footer lines
5. **Map mnemonic** from title (text before `—`) to existing `InstructionRecord` by mnemonic

### Mnemonic Matching

The PDF title mnemonic (e.g., `ADDPS`, `VADDPS`) maps to potentially many `InstructionRecord`s that share the same base mnemonic. All variants of that mnemonic share the same description sections. Matching is case-insensitive on the mnemonic prefix.

## Download & Caching

- **Stable URL:** `https://cdrdv2.intel.com/v1/dl/getContent/671200` (no auth, ~25MB)
- **Local cache:** `vendor/intel/intel-sdm.pdf` (gitignored, never committed)
- **Fallback order:** local vendor PDF -> download from Intel CDN -> skip (descriptions empty)
- The PDF download is optional; if it fails, `update` still succeeds with empty descriptions

## CLI Changes

### Pager Output

Rich's `console.pager()` context manager pipes output through the system pager (`less -R` on Linux/macOS, `more` on Windows). Applied when rendering detailed views (intrinsic or instruction).

Cross-platform: Rich handles pager detection automatically.

### `--short` / `-s` Flag

Global flag that suppresses description sections in detail views. Only shows the one-line summary + performance tables.

```
simdref ADDPS           # full view with pager (description + operation + perf)
simdref -s ADDPS        # short: summary + perf tables only, no pager
simdref --short ADDPS   # same as -s
```

### Display Changes

`render_instruction_sections()` and `render_intrinsic()` gain a `short: bool` parameter. When `short=False` (default), description sections render as Rich Panels below the metadata, each with a section title.

## Web UI Changes

### Expandable Description Sections

In `renderInstructionDetail()` and `renderIntrinsicDetail()` in `app.js`, add collapsible `<details>` elements for each description section, matching the existing `meas-group` pattern used for performance data:

```html
<details class="desc-section" open>
  <summary>Description</summary>
  <div class="desc-body">...</div>
</details>
<details class="desc-section">
  <summary>Operation</summary>
  <pre class="desc-code">...</pre>
</details>
```

The Description section defaults to open; Operation and others default to closed.

### Data Flow

- `description` dict included in detail-chunks JSON (instruction details)
- `description` dict included in intrinsic-details JSON  
- Search index unchanged (uses `summary` one-liner)

## Storage Changes

### SQLite Schema

Bump `SQLITE_SCHEMA_VERSION` to `"4"`. The `description` field is stored inside the JSON `payload` column (no new columns needed). The schema version bump triggers automatic rebuild.

### Backward Compatibility

`InstructionRecord.from_dict()` (via `Catalog.from_dict()`) already uses `**item`, so old catalogs without `description` will default to `{}` via `field(default_factory=dict)`.

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `src/simdref/pdfparse/__init__.py` | Create | Package init, public API |
| `src/simdref/pdfparse/base.py` | Create | Base PDF section extractor (font-based heading detection) |
| `src/simdref/pdfparse/intel.py` | Create | Intel SDM-specific: title pattern, section names, page range |
| `src/simdref/models.py` | Modify | Add `description: dict[str, str]` to `InstructionRecord` |
| `src/simdref/ingest.py` | Modify | Add Intel SDM PDF fetch + parse + merge descriptions |
| `src/simdref/storage.py` | Modify | Bump schema version to 4 |
| `src/simdref/display.py` | Modify | Render description sections, pager support |
| `src/simdref/cli.py` | Modify | Add `--short`/`-s` flag, pager wrapping |
| `src/simdref/web.py` | Modify | Include `description` in detail chunks |
| `src/simdref/manpages.py` | Modify | Use description sections in man pages |
| `src/simdref/templates/app.js` | Modify | Render expandable description sections |
| `src/simdref/templates/style.css` | Modify | Style for `.desc-section`, `.desc-body`, `.desc-code` |
| `pyproject.toml` | Modify | Add `pdfplumber` dependency |
| `.gitignore` | Modify | Add `vendor/intel/*.pdf` pattern |
| `tests/test_pdfparse.py` | Create | Tests for PDF parsing |
| `tests/test_description_display.py` | Create | Tests for description rendering |

## Testing Strategy

- **Unit tests for PDF parsing:** Use a fixture PDF (a few pages extracted from the Intel SDM, or a synthetic PDF created with reportlab) to test section extraction
- **Integration test:** Verify that `build_catalog()` produces `InstructionRecord`s with populated `description` when PDF is available
- **Display tests:** Verify `render_instruction_sections(short=True)` omits description, `short=False` includes it
- **Web export tests:** Verify description appears in detail chunks JSON

## Out of Scope (Future)

- ARM Architecture Reference Manual parsing
- RISC-V ISA manual parsing
- Table extraction from opcode encoding tables (kept as-is from uops.info)
- SVG figure extraction from PDF
