"""Intel SDM PDF parser.

Extracts per-instruction description sections from the Intel 64 and IA-32
Architectures Software Developer's Manual (combined volumes PDF).

The parser identifies instruction pages by their size-12 title font with
an all-caps mnemonic before an em-dash, then extracts size-10 section
headings and size-9 body text within each instruction's page range.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

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

# Canonical section names and aliases.
KNOWN_SECTIONS: set[str] = {
    "Description",
    "Operation",
    "Intrinsic Equivalents",
    "Flags Affected",
    "Exceptions",
    "Numeric Exceptions",
    "SIMD Floating-Point Exceptions",
    "Other Exceptions",
    "Protected Mode Exceptions",
    "Real-Address Mode Exceptions",
    "Virtual-8086 Mode Exceptions",
    "Compatibility Mode Exceptions",
    "64-Bit Mode Exceptions",
}

_SECTION_ALIASES: dict[str, str] = {
    "intel c/c++ compiler intrinsic equivalent": "Intrinsic Equivalents",
    "intel c/c++compiler intrinsic equivalent": "Intrinsic Equivalents",
    "c/c++ compiler intrinsic equivalent": "Intrinsic Equivalents",
    "intrinsic equivalent": "Intrinsic Equivalents",
    "instruction operand encoding": "Operand Encoding",
}

# Footer pattern: "MNEMONIC—... Vol. 2X N-NN"
_FOOTER_RE = re.compile(r"^.+Vol\.\s*2[A-D]?\s+\d+-\d+$")


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


def parse_intel_sdm(pdf_path: Path) -> dict[str, dict[str, str]]:
    """Parse the Intel SDM PDF and return per-mnemonic description sections.

    Returns a dict mapping uppercase mnemonic to a dict of section name -> text.
    Mnemonics with ``/`` separators are expanded so each variant maps to the same dict.
    """
    import pdfplumber

    log.info("parsing Intel SDM: %s", pdf_path)
    pdf = pdfplumber.open(pdf_path)
    total = len(pdf.pages)
    log.info("total pages: %d", total)

    # Phase 1: identify instruction title pages
    title_pages: list[tuple[int, str, str]] = []
    for i in range(total):
        chars = pdf.pages[i].chars
        title_chars = [c for c in chars if c["size"] >= _TITLE_MIN_SIZE]
        if not title_chars:
            continue
        title_text = "".join(c["text"] for c in title_chars).strip()
        parsed = parse_instruction_title(title_text)
        if parsed is not None:
            title_pages.append((i, parsed[0], parsed[1]))

    log.info("found %d instruction title pages", len(title_pages))

    # Phase 2: extract sections for each instruction
    result: dict[str, dict[str, str]] = {}
    for idx, (page_start, mnemonic, _summary) in enumerate(title_pages):
        page_end = title_pages[idx + 1][0] if idx + 1 < len(title_pages) else min(page_start + 10, total)

        all_chars: list[dict] = []
        for page_idx in range(page_start, page_end):
            page_chars = pdf.pages[page_idx].chars
            for c in page_chars:
                if c["size"] < _TITLE_MIN_SIZE:
                    all_chars.append(c)

        raw_sections = extract_sections_from_chars(
            all_chars,
            heading_min_size=_HEADING_MIN_SIZE,
            body_max_size=_BODY_MAX_SIZE,
        )

        sections: dict[str, str] = {}
        for heading, body in raw_sections.items():
            canonical = normalize_section_name(heading)
            lines = [line for line in body.split("\n") if not _FOOTER_RE.match(line)]
            cleaned = "\n".join(lines).strip()
            if cleaned:
                sections[canonical] = cleaned

        for part in mnemonic.split("/"):
            part = part.strip()
            if part:
                result[part.upper()] = sections

    pdf.close()
    log.info("extracted descriptions for %d mnemonics", len(result))
    return result
