"""Intel SDM PDF parser.

Extracts per-instruction description sections from the Intel 64 and IA-32
Architectures Software Developer's Manual (combined volumes PDF).

The parser identifies instruction pages by their size-12 title font with
an all-caps mnemonic before an em-dash, then extracts size-10 section
headings and size-9 body text within each instruction's page range.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

from simdref.pdfparse.base import extract_sections_from_chars

log = logging.getLogger(__name__)

INTEL_SDM_URL = "https://cdrdv2.intel.com/v1/dl/getContent/671200"

# Title pattern: ALL-CAPS mnemonic (with optional / separators) before em-dash.
_TITLE_RE = re.compile(r"^([A-Z][A-Z0-9/_\s]{0,80})\s*\u2014\s*(.+)")

# Words that indicate a chapter/section heading, not an instruction.
_SKIP_WORDS = frozenset({"CHAPTER", "CONTENTS", "APPENDIX", "VOLUME", "INSTRUCTION SET REFERENCE"})

# Font size thresholds (from empirical analysis of Intel SDM).
_TITLE_MIN_SIZE = 11.5
_HEADING_MIN_SIZE = 9.8
_BODY_MAX_SIZE = 9.5
_BODY_MIN_SIZE = 8.0  # Filter superscripts (®, footnote markers)

# Canonical section names and aliases.
KNOWN_SECTIONS: set[str] = {
    "Description",
    "Operation",
    "Intrinsic Equivalents",
    "Flags Affected",
    "FPU Flags Affected",
    "Exceptions",
    "Numeric Exceptions",
    "SIMD Floating-Point Exceptions",
    "Floating-Point Exceptions",
    "Other Exceptions",
    "Other Mode Exceptions",
    "Protected Mode Exceptions",
    "Real-Address Mode Exceptions",
    "Real Address Mode Exceptions",
    "Virtual-8086 Mode Exceptions",
    "Virtual-8086 Exceptions",
    "Virtual 8086 Mode Exceptions",
    "Compatibility Mode Exceptions",
    "64-Bit Mode Exceptions",
    "x87 FPU and SIMD Floating-Point Exceptions",
}

_SECTION_ALIASES: dict[str, str] = {
    "intel c/c++ compiler intrinsic equivalent": "Intrinsic Equivalents",
    "intel c/c++ compiler intrinsic equivalents": "Intrinsic Equivalents",
    "intel c/c++compiler intrinsic equivalent": "Intrinsic Equivalents",
    "intel c/c++compiler intrinsic equivalents": "Intrinsic Equivalents",
    "c/c++ compiler intrinsic equivalent": "Intrinsic Equivalents",
    "c/c++ compiler intrinsic equivalents": "Intrinsic Equivalents",
    "intrinsic equivalent": "Intrinsic Equivalents",
    "intrinsic equivalents": "Intrinsic Equivalents",
    "instruction operand encoding": "Operand Encoding",
    "fpu flags affected": "FPU Flags Affected",
    "floating-point exceptions": "Floating-Point Exceptions",
}

# Footer pattern: "MNEMONIC—... Vol. 2X N-NN" (space between volume and page optional)
_FOOTER_RE = re.compile(r"^.+Vol\.\s*2[A-D]?\s*\d+-\d+$")

# Sections to discard (tabular data that doesn't render well as text).
_DISCARD_SECTIONS: frozenset[str] = frozenset({
    "Instruction Operand Encoding",
    "Operand Encoding",
})

# Sections whose text is pseudocode and should preserve indentation from x0.
_CODE_SECTIONS: frozenset[str] = frozenset({
    "Operation",
    "Intrinsic Equivalents",
})

# All heading names (lowercase) for content-based heading detection.
# Some Intel SDM pages format section headings at body text size.
_ALL_HEADING_NAMES: frozenset[str] = frozenset(
    {s.lower() for s in KNOWN_SECTIONS}
    | set(_SECTION_ALIASES.keys())
    | {s.lower() for s in _DISCARD_SECTIONS}
)

# Minimal safety-net for junk lines that appear outside table bounding boxes.
_JUNK_LINE_RE = re.compile(
    r"^\d+\.\s+See note "             # footnote references (1. See note ...)
    r"|^NOTES:\s*$"                     # trailing NOTES: line
)


# Maximum fraction of page area a single table can cover before we
# consider it a false-positive detection by pdfplumber.
_TABLE_MAX_PAGE_FRACTION = 0.85


def normalize_section_name(raw: str) -> str:
    """Map a raw heading string to its canonical section name."""
    stripped = raw.strip()
    lowered = stripped.lower()
    if lowered in _SECTION_ALIASES:
        return _SECTION_ALIASES[lowered]
    for known in KNOWN_SECTIONS:
        if lowered == known.lower():
            return known
    return stripped


def parse_instruction_title(text: str) -> tuple[str, str] | None:
    """Parse an instruction title into (mnemonic, summary).

    Returns None if the text is not an instruction title.
    """
    text = text.strip()
    m = _TITLE_RE.match(text)
    if m is None:
        return None
    mnemonic = m.group(1).strip()
    summary = m.group(2).strip()
    if any(word in mnemonic for word in _SKIP_WORDS):
        return None
    alpha = [c for c in mnemonic if c.isalpha()]
    if not alpha:
        return None
    if sum(1 for c in alpha if c.isupper()) / len(alpha) < 0.9:
        return None
    return mnemonic, summary


def parse_intel_sdm(
    pdf_path: Path,
    *,
    status: Callable[[str], None] | None = None,
) -> dict[str, dict[str, object]]:
    """Parse the Intel SDM PDF and return per-mnemonic description payloads.

    Returns a dict mapping uppercase mnemonic to a payload with:
    * ``sections``: dict of section name -> text
    * ``page_start``: 1-based first page in the PDF
    * ``page_end``: 1-based last page in the PDF

    Mnemonics with ``/`` separators are expanded so each variant maps to the
    same payload.
    """
    import pdfplumber

    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

    log.info("parsing Intel SDM: %s", pdf_path)
    pdf = pdfplumber.open(pdf_path)
    total = len(pdf.pages)
    log.info("total pages: %d", total)

    interactive_progress = sys.stderr.isatty() and os.environ.get("GITHUB_ACTIONS") != "true"
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        transient=True,
    )
    if interactive_progress:
        progress.start()
    if status is not None:
        status(f"Opened Intel SDM PDF with {total} pages")

    # Phase 1: identify instruction title pages (fast — no table detection).
    scan_task = progress.add_task("Scanning pages", total=total) if interactive_progress else None
    title_pages: list[tuple[int, str, str]] = []
    for i in range(total):
        page = pdf.pages[i]
        chars = page.chars
        title_chars = [c for c in chars if c["size"] >= _TITLE_MIN_SIZE]
        if title_chars:
            title_text = "".join(c["text"] for c in title_chars).strip()
            parsed = parse_instruction_title(title_text)
            if parsed is not None:
                title_pages.append((i, parsed[0], parsed[1]))
        if interactive_progress and scan_task is not None:
            progress.advance(scan_task)
        elif status is not None and ((i + 1) % 250 == 0 or i + 1 == total):
            status(f"Scanning SDM title pages: {i + 1}/{total}")

    log.info("found %d instruction title pages", len(title_pages))
    if status is not None:
        status(f"Found {len(title_pages)} instruction title pages in Intel SDM")

    # Phase 2: extract sections for each instruction.
    # Table bounding boxes are computed on-demand (only instruction pages).
    extract_task = progress.add_task("Extracting descriptions", total=len(title_pages)) if interactive_progress else None
    page_table_bboxes: dict[int, list[tuple[float, float, float, float]]] = {}
    result: dict[str, dict[str, object]] = {}
    for idx, (page_start, mnemonic, _summary) in enumerate(title_pages):
        page_end = title_pages[idx + 1][0] if idx + 1 < len(title_pages) else min(page_start + 10, total)

        all_chars: list[dict] = []
        for page_idx in range(page_start, page_end):
            page_chars = pdf.pages[page_idx].chars
            # Lazily compute and cache table bboxes for this page.
            if page_idx not in page_table_bboxes:
                tables = pdf.pages[page_idx].find_tables()
                if tables:
                    page_area = pdf.pages[page_idx].width * pdf.pages[page_idx].height
                    bboxes_list = []
                    for t in tables:
                        bx0, by0, bx1, by1 = t.bbox
                        table_area = (bx1 - bx0) * (by1 - by0)
                        if table_area / page_area < _TABLE_MAX_PAGE_FRACTION:
                            bboxes_list.append((bx0, by0, bx1, by1))
                    page_table_bboxes[page_idx] = bboxes_list
                else:
                    page_table_bboxes[page_idx] = []
            bboxes = page_table_bboxes[page_idx]
            for c in page_chars:
                if c["size"] >= _TITLE_MIN_SIZE or c["size"] < _BODY_MIN_SIZE:
                    continue
                # Exclude characters inside table bounding boxes.
                if bboxes and any(
                    bbox[0] <= c["x0"] <= bbox[2] and bbox[1] <= c["top"] <= bbox[3]
                    for bbox in bboxes
                ):
                    continue
                all_chars.append(c)

        raw_sections = extract_sections_from_chars(
            all_chars,
            heading_min_size=_HEADING_MIN_SIZE,
            body_max_size=_BODY_MAX_SIZE,
            known_headings=_ALL_HEADING_NAMES,
        )

        sections: dict[str, str] = {}
        for heading, line_tuples in raw_sections.items():
            canonical = normalize_section_name(heading)
            if canonical in _DISCARD_SECTIONS:
                continue
            # Filter footer and residual junk lines.
            filtered = [
                (x0, text) for x0, text in line_tuples
                if not _FOOTER_RE.match(text) and not _JUNK_LINE_RE.match(text)
            ]
            if not filtered:
                continue
            if canonical in _CODE_SECTIONS:
                # Reconstruct indentation from x0 positions.
                min_x0 = min(x0 for x0, _ in filtered)
                indent_unit = 18.0  # ~18pt per indent level in Intel SDM
                out_lines = []
                for x0, text in filtered:
                    level = round((x0 - min_x0) / indent_unit)
                    out_lines.append("    " * level + text)
                cleaned = "\n".join(out_lines).strip()
            else:
                # Filter footnote lines between tables that have a
                # significantly different left-edge position (x0).
                if len(filtered) > 3:
                    x0_counts: Counter[int] = Counter(
                        round(x0) for x0, _ in filtered
                    )
                    dominant_x0 = x0_counts.most_common(1)[0][0]
                    filtered = [
                        (x0, t) for x0, t in filtered
                        if abs(round(x0) - dominant_x0) <= 3
                    ]
                prose_lines = [text for _, text in filtered]
                # Join PDF-wrapped lines into paragraphs.  A new paragraph
                # starts when the previous line ends with sentence-terminal
                # punctuation.  Bullet/numbered list items also start new
                # paragraphs.
                paragraphs: list[str] = []
                for line in prose_lines:
                    if not paragraphs:
                        paragraphs.append(line)
                    elif paragraphs[-1][-1:] in ".):;":
                        paragraphs.append(line)
                    elif line[:1] in ("\u2022", "\u2013") or re.match(r"^\d+\.\s", line):
                        # Bullet points or numbered list items.
                        paragraphs.append(line)
                    elif paragraphs[-1].endswith("-"):
                        # De-hyphenate word breaks (e.g. "indi-\ncate").
                        paragraphs[-1] = paragraphs[-1][:-1] + line
                    else:
                        paragraphs[-1] += " " + line
                cleaned = "\n".join(paragraphs).strip()
            if cleaned:
                sections[canonical] = cleaned

        payload = {
            "sections": sections,
            "page_start": page_start + 1,
            "page_end": page_end,
        }
        for part in mnemonic.split("/"):
            part = part.strip()
            if part:
                result[part.upper()] = payload
        if interactive_progress and extract_task is not None:
            progress.advance(extract_task)
        elif status is not None and ((idx + 1) % 50 == 0 or idx + 1 == len(title_pages)):
            status(f"Extracting SDM descriptions: {idx + 1}/{len(title_pages)} instructions")

    if interactive_progress:
        progress.stop()
    pdf.close()
    log.info("extracted descriptions for %d mnemonics", len(result))
    if status is not None:
        status(f"Extracted SDM descriptions for {len(result)} mnemonic variants")
    return result
