"""Roff manpage generation for intrinsics and instructions.

Generates man7 pages that can be viewed with ``man -M share/man <name>``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from simdref.models import Catalog, InstructionRecord, IntrinsicRecord
from simdref.queries import instruction_rows_for_intrinsic


def _roff_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("-", "\\-")


def _section(title: str, body: str) -> str:
    return f".SH {title}\n{body}\n"


def _metric_lines(record: InstructionRecord) -> list[str]:
    lines = []
    for arch, values in sorted(record.metrics.items()):
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(values.items()))
        lines.append(f"{arch}: {rendered}")
    return lines


def _instruction_perf_lines(catalog: Catalog, intrinsic: IntrinsicRecord) -> list[str]:
    """Format linked instruction performance data as text lines for man pages."""
    lines = []
    for row in instruction_rows_for_intrinsic(catalog, intrinsic):
        instruction = row.get("instruction", "-")
        uarch = row.get("uarch", "-")
        metrics = {k: v for k, v in row.items() if k not in {"instruction", "uarch"}}
        if metrics and uarch != "-":
            rendered = ", ".join(f"{key}={value}" for key, value in sorted(metrics.items()))
            lines.append(f"{instruction} | {uarch}: {rendered}")
        else:
            lines.append(f"{instruction} | no performance metrics available")
    return lines


def intrinsic_page(record: IntrinsicRecord, catalog: Catalog) -> str:
    parts = [f'.TH "{record.name}" "7" "simdref" "simdref" "SIMD Intrinsic Reference"\n']
    parts.append(_section("NAME", f"{_roff_escape(record.name)} \\- {_roff_escape(record.description or 'intrinsic')}"))
    parts.append(_section("SYNOPSIS", f".nf\n{_roff_escape(record.signature)}\n.fi"))
    parts.append(_section("DESCRIPTION", _roff_escape(record.description or "No description available.")))
    parts.append(_section("HEADER", _roff_escape(record.header or "Unknown")))
    parts.append(_section("ISA", _roff_escape(", ".join(record.isa) or "Unknown")))
    parts.append(_section("CATEGORY", _roff_escape(record.category or "Unknown")))
    parts.append(_section("INSTRUCTIONS", _roff_escape(", ".join(record.instructions) or "None linked")))
    parts.append(_section("PERFORMANCE SUMMARY", _roff_escape("\n".join(_instruction_perf_lines(catalog, record)) or "No performance metrics available.")))
    parts.append(_section("PERFORMANCE DETAILS", _roff_escape("\n".join(_instruction_perf_lines(catalog, record)) or "No performance metrics available.")))
    parts.append(_section("NOTES", _roff_escape("; ".join(record.notes) or "None")))
    parts.append(_section("SEE ALSO", _roff_escape(", ".join(record.instructions) or "simdref-search(7)")))
    return "".join(parts)


def instruction_page(record: InstructionRecord) -> str:
    parts = [f'.TH "{record.mnemonic}" "7" "simdref" "simdref" "SIMD Instruction Reference"\n']
    parts.append(_section("NAME", f"{_roff_escape(record.key)} \\- {_roff_escape(record.summary)}"))
    parts.append(_section("DESCRIPTION", _roff_escape(record.summary)))
    parts.append(_section("ISA", _roff_escape(", ".join(record.isa) or "Unknown")))
    parts.append(_section("OPERANDS", _roff_escape("\n".join(record.operands) or "No operand details available.")))
    parts.append(_section("INTRINSICS", _roff_escape(", ".join(record.linked_intrinsics) or "None linked")))
    parts.append(_section("PERFORMANCE DETAILS", _roff_escape("\n".join(_metric_lines(record)) or "No performance metrics available.")))
    return "".join(parts)


def write_manpages(catalog: Catalog, man_dir: Path) -> None:
    section_dir = man_dir / "man7"
    section_dir.mkdir(parents=True, exist_ok=True)
    for intrinsic in catalog.intrinsics:
        (section_dir / f"{intrinsic.name}.7").write_text(intrinsic_page(intrinsic, catalog))
    for instruction in catalog.instructions:
        filename = f"instruction-{record_slug(instruction.key)}.7"
        (section_dir / filename).write_text(instruction_page(instruction))
        (section_dir / f"{instruction.mnemonic}.7").write_text(instruction_page(instruction))


def record_slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


def open_manpage(name: str, man_dir: Path) -> int:
    target = man_dir / "man7" / f"{name}.7"
    if not target.exists():
        return 1
    return subprocess.call(["man", "-M", str(man_dir), name])
