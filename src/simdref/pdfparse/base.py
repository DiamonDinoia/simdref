"""Base PDF section extractor using pdfplumber character-level font metadata.

Detects section headings by font size and accumulates body text under each
heading. ISA-specific modules configure the size thresholds and heading
patterns.
"""

from __future__ import annotations


def _chars_to_lines(chars: list[dict]) -> list[tuple[float, float, float, str]]:
    """Group characters into lines by vertical position.

    Returns a list of ``(top, size, x0, text)`` tuples sorted by vertical
    position. Characters on the same line (within 2pt vertical tolerance)
    are concatenated. ``x0`` is the left-edge position of the first
    character, which encodes indentation level.
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

    result: list[tuple[float, float, float, str]] = []
    for top, line_chars in lines:
        # Build text with gap-based space insertion.  When consecutive
        # characters have a large horizontal gap (>10pt) a space is
        # inserted to handle two-column layouts (e.g. "#IS  Stack
        # underflow occurred." in Intel SDM exception tables).
        parts: list[str] = []
        prev_right = -1.0
        for c in line_chars:
            if prev_right >= 0 and c["x0"] - prev_right > 10.0:
                if parts and not parts[-1].endswith(" "):
                    parts.append(" ")
            parts.append(c["text"])
            prev_right = c["x0"] + c.get("width", 0)
        text = "".join(parts).strip()
        max_size = max(c["size"] for c in line_chars)
        x0 = min(c["x0"] for c in line_chars)
        if text:
            result.append((top, max_size, x0, text))
    return result


def extract_sections_from_chars(
    chars: list[dict],
    heading_min_size: float,
    body_max_size: float,
    known_headings: frozenset[str] | set[str] | None = None,
) -> dict[str, list[tuple[float, str]]]:
    """Extract named sections from a list of pdfplumber character dicts.

    Characters with font size >= *heading_min_size* are treated as section
    headings. Characters with font size <= *body_max_size* are accumulated
    as body text under the current heading.

    If *known_headings* is provided, heading detection switches to
    **whitelist mode**: a line is a heading ONLY if its lowercased text
    matches the known headings set.  Lines at heading font size that do
    NOT match are demoted to body text under the current heading.  When
    *known_headings* is ``None``, the original font-size-only heuristic
    is used (backward compatibility).

    Returns a dict mapping heading text to a list of ``(x0, text)`` tuples
    preserving the left-edge position for indentation reconstruction.
    """
    lines = _chars_to_lines(chars)
    sections: dict[str, list[tuple[float, str]]] = {}
    current_heading: str | None = None
    body_parts: list[tuple[float, str]] = []

    for _top, size, line_x0, text in lines:
        if known_headings is not None:
            is_heading = text.lower().strip() in known_headings
        else:
            is_heading = size >= heading_min_size
        if is_heading:
            if current_heading is not None and body_parts:
                sections[current_heading] = body_parts
            current_heading = text
            body_parts = []
        elif size <= body_max_size and current_heading is not None:
            body_parts.append((line_x0, text))

    if current_heading is not None and body_parts:
        sections[current_heading] = body_parts

    return sections
