"""Arm instruction-source parsing and normalization."""

from __future__ import annotations

import json
import re
from typing import Any

from simdref.models import InstructionRecord


def _normalize_isa(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,/|]\s*|\s{2,}", value) if part.strip()]
    return []


def _canonical_instruction_key(name: str, form: str) -> str:
    instruction_name = name.strip().upper()
    instruction_form = re.sub(r"\s*,\s*", ", ", form.strip())
    instruction_form = re.sub(r"\s+", " ", instruction_form)
    if not instruction_name:
        return ""
    if not instruction_form:
        return instruction_name
    return f"{instruction_name} ({instruction_form.upper()})"


def _generated_summary(mnemonic: str) -> str:
    core = mnemonic.strip().upper()
    if not core:
        return "Arm instruction."
    if core.startswith("ADD"):
        return "Add operands."
    if core.startswith("SUB"):
        return "Subtract operands."
    if core.startswith("MUL"):
        return "Multiply operands."
    if core.startswith("LD"):
        return "Load data."
    if core.startswith("ST"):
        return "Store data."
    return f"{core.title()} instruction."


def _strip_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value", "body", "summary", "description", "brief"):
            text = _strip_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts = [_strip_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    return ""


def _collapse_section_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_collapse_section_value(item) for item in value]
        return "\n\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        if "title" in value and any(key in value for key in ("body", "content", "text", "value")):
            title = _strip_text(value.get("title"))
            body = _strip_text(value.get("body") or value.get("content") or value.get("text") or value.get("value"))
            return "\n".join(part for part in (title, body) if part).strip()
        flattened = []
        for item in value.values():
            text = _collapse_section_value(item)
            if text:
                flattened.append(text)
        return "\n\n".join(flattened).strip()
    return ""


def _description_sections(item: dict[str, Any]) -> dict[str, str]:
    sections: dict[str, str] = {}

    for key in ("description", "descriptions", "doc_sections", "sections"):
        raw = item.get(key)
        if isinstance(raw, dict):
            for title, value in raw.items():
                text = _collapse_section_value(value)
                if text:
                    sections[str(title).strip()] = text
        elif isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                title = _strip_text(entry.get("title") or entry.get("name") or entry.get("heading"))
                body = _collapse_section_value(entry.get("body") or entry.get("content") or entry.get("text") or entry.get("value"))
                if title and body:
                    sections[title] = body

    operation = _collapse_section_value(item.get("operation") or item.get("pseudocode"))
    if operation:
        sections.setdefault("Operation", operation)

    detail = _strip_text(item.get("detail") or item.get("details"))
    if detail:
        sections.setdefault("Details", detail)

    return sections


def _infer_arm_isa(item: dict[str, Any]) -> list[str]:
    explicit = _normalize_isa(item.get("isa") or item.get("isas") or item.get("extensions") or item.get("feature_tags"))
    if explicit:
        return explicit

    haystack_parts = [
        _strip_text(item.get("category")),
        _strip_text(item.get("section")),
        _strip_text(item.get("group")),
        _strip_text(item.get("classification")),
        _strip_text(item.get("url")),
    ]
    haystack = " ".join(part.upper() for part in haystack_parts if part)

    isa: list[str] = []
    for token in ("SME2", "SME", "SVE2", "SVE", "MVE", "HELIUM", "NEON", "ADVSIMD", "SIMD-FP", "SIMD&FP"):
        if token in haystack:
            if token in {"HELIUM", "MVE"}:
                candidate = "MVE"
            elif token in {"ADVSIMD", "SIMD-FP", "SIMD&FP"}:
                candidate = "NEON"
            else:
                candidate = token
            if candidate not in isa:
                isa.append(candidate)

    if isa:
        return isa
    return ["A64"]


def _collect_aliases(item: dict[str, Any]) -> list[str]:
    raw = item.get("aliases") or item.get("alias_mnemonics") or []
    if isinstance(raw, str):
        raw = [raw]
    aliases = [str(value).strip() for value in raw if str(value).strip()]
    alias = _strip_text(item.get("alias"))
    if alias:
        aliases.append(alias)
    return list(dict.fromkeys(aliases))


def _collect_metadata(item: dict[str, Any], *, url: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for source_key, target_key in (
        ("category", "category"),
        ("section", "section"),
        ("group", "group"),
        ("classification", "classification"),
        ("encoding", "encoding"),
        ("operand_form", "operand_form"),
        ("instruction_class", "instruction_class"),
        ("architecture_state", "architecture_state"),
    ):
        value = _strip_text(item.get(source_key))
        if value:
            metadata[target_key] = value
    if url:
        metadata["url"] = url
    return metadata


def _normalize_instruction_item(item: dict[str, Any]) -> InstructionRecord | None:
    mnemonic = _strip_text(
        item.get("mnemonic")
        or item.get("base_instruction")
        or item.get("instruction")
        or item.get("name")
        or item.get("opcode")
    ).upper()
    if not mnemonic:
        return None

    operands = _strip_text(item.get("operands") or item.get("operand_form") or item.get("form"))
    form = _canonical_instruction_key(mnemonic, operands) if operands and not operands.upper().startswith(f"{mnemonic} (") else operands
    summary = _strip_text(item.get("summary") or item.get("brief") or item.get("title")) or _generated_summary(mnemonic)
    if not summary.endswith("."):
        summary += "."
    url = _strip_text(item.get("url") or item.get("source_url") or item.get("reference_url"))
    return InstructionRecord(
        mnemonic=mnemonic,
        form=form,
        summary=summary,
        architecture="arm",
        isa=_infer_arm_isa(item),
        metadata=_collect_metadata(item, url=url),
        aliases=_collect_aliases(item),
        description=_description_sections(item),
        source="arm-a64",
    )


def _candidate_instruction_lists(payload: Any) -> list[list[dict[str, Any]]]:
    lists: list[list[dict[str, Any]]] = []
    if isinstance(payload, list):
        if all(isinstance(item, dict) for item in payload):
            lists.append(payload)
        return lists
    if not isinstance(payload, dict):
        return lists

    for key in (
        "instructions",
        "base_instructions",
        "instruction_set",
        "InstructionSet",
        "items",
        "records",
    ):
        value = payload.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            lists.append(value)

    for value in payload.values():
        if isinstance(value, dict):
            lists.extend(_candidate_instruction_lists(value))

    return lists


def _records_from_payload(payload: Any) -> list[InstructionRecord]:
    if isinstance(payload, dict) and payload.get("format") == "arm-aarchmrs-instructions-v1":
        instructions_json = str(payload.get("instructions_json") or "[]")
        return _records_from_payload(json.loads(instructions_json))

    if isinstance(payload, dict) and payload.get("format") == "arm-instructions-fixture-v1":
        return _records_from_payload(payload.get("instructions") or [])

    for candidates in _candidate_instruction_lists(payload):
        records = [_normalize_instruction_item(item) for item in candidates]
        normalized = [record for record in records if record is not None]
        if normalized:
            return normalized
    return []


def parse_arm_instruction_payload(text: str) -> list[InstructionRecord]:
    payload = json.loads(text)
    return _records_from_payload(payload)
