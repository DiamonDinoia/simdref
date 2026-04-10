"""Base PDF section extractor using pdfplumber character-level font metadata.

Detects section headings by font size and accumulates body text under each
heading. ISA-specific modules configure the size thresholds and heading
patterns.
"""

from __future__ import annotations


def _chars_to_lines(chars: list[dict]) -> list[tuple[float, float, str]]:
    """Group characters into lines by vertical position.

    Returns a list of ``(top, size, text)`` tuples sorted by vertical
    position. Characters on the same line (within 2pt vertical tolerance)
    are concatenated.
    """
    if not chars:
        return []
    lines: list[tuple[float, list[dict]]] = []
    current_top = chars[0]["top"]
    current_chars: list[dict] = [chars[0]]

    for c in chars[1:]:
        if abs(c["top"] - current_top) > 2.0:
            lines.append((current_top, current_chars))
            current_top = c["top"]
            current_chars = [c]
        else:
            current_chars.append(c)

    lines.append((current_top, current_chars))

    result: list[tuple[float, float, str]] = []
    for top, line_chars in lines:
        text = "".join(c["text"] for c in line_chars).strip()
        max_size = max(c["size"] for c in line_chars)
        if text:
            result.append((top, max_size, text))
    return result


def extract_sections_from_chars(
    chars: list[dict],
    heading_min_size: float,
    body_max_size: float,
) -> dict[str, str]:
    """Extract named sections from a list of pdfplumber character dicts.

    Characters with font size >= *heading_min_size* are treated as section
    headings. Characters with font size <= *body_max_size* are accumulated
    as body text under the current heading.

    Returns a dict mapping heading text to accumulated body text.
    """
    lines = _chars_to_lines(chars)
    sections: dict[str, str] = {}
    current_heading: str | None = None
    body_parts: list[str] = []

    for _top, size, text in lines:
        if size >= heading_min_size:
            if current_heading is not None and body_parts:
                sections[current_heading] = "\n".join(body_parts).strip()
            current_heading = text
            body_parts = []
        elif size <= body_max_size and current_heading is not None:
            body_parts.append(text)

    if current_heading is not None and body_parts:
        sections[current_heading] = "\n".join(body_parts).strip()

    return sections
