"""Catalog parsing, linking, and assembly."""

from __future__ import annotations

import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from simdref.ingest_pdf import find_pdf_source_path, load_or_parse_pdf_source, merge_pdf_enrichment
from simdref.ingest_sources import fetch_intel_data, fetch_uops_xml, now_iso
from simdref.models import Catalog, InstructionRecord, IntrinsicRecord

_UOPS_METADATA_KEYS = frozenset({"category", "cpl", "extension", "iclass", "iform", "url", "url-ref"})
_UOPS_OPERAND_KEYS = ("idx", "r", "w", "type", "width", "xtype", "name")


def _iter_xml_elements(source: str | Path, tag: str):
    context = ET.iterparse(io.StringIO(source) if isinstance(source, str) else source, events=("start", "end"))
    root = None
    for event, elem in context:
        if event == "start" and root is None:
            root = elem
            continue
        if event != "end" or elem.tag != tag:
            continue
        yield elem
        elem.clear()
        if root is not None:
            root.clear()


def _intern_small(value: str) -> str:
    return sys.intern(value) if value and len(value) <= 32 else value


def _normalize_isa(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_intern_small(str(item)) for item in value if item]
    if isinstance(value, str):
        return list(_normalize_isa_string(value))
    return []


@lru_cache(maxsize=512)
def _normalize_isa_string(value: str) -> tuple[str, ...]:
    parts = re.split(r"[,/|]\s*|\s{2,}", value)
    return tuple(_intern_small(part.strip()) for part in parts if part.strip())


def _canonical_instruction_key(name: str, form: str) -> str:
    instruction_name = name.strip().upper()
    instruction_form = form.strip()
    if not instruction_name:
        return ""
    if not instruction_form:
        return instruction_name
    return f"{instruction_name} ({instruction_form.upper()})"


@lru_cache(maxsize=128)
def _normalize_operand_xtype(value: str) -> str:
    xtype = value.strip()
    if not xtype:
        return xtype
    if xtype == "int":
        return "i32"
    match = re.fullmatch(r"\d+([iu]\d+)", xtype)
    if match:
        return match.group(1)
    return xtype


def _summary_too_terse(summary: str, mnemonic: str) -> bool:
    text = summary.strip().strip(".")
    if not text:
        return True
    words = re.findall(r"[A-Za-z0-9+\-]+", text)
    if len(words) <= 1:
        return True
    if text.casefold() == mnemonic.casefold():
        return True
    if len(words) == 2 and words[0].casefold() == words[1].casefold() == mnemonic.casefold():
        return True
    return False


def _summary_prefix(mnemonic: str, operand_details: list[dict[str, str]], summary: str) -> str:
    upper = mnemonic.upper()
    lowered = summary.casefold()
    has_mask_operand = any(op.get("xtype") == "i1" for op in operand_details)
    if upper.endswith("_Z") and "zero-mask" not in lowered and "zeromask" not in lowered and "zeroing" not in lowered:
        return "Zero-masked "
    if has_mask_operand and "mask" not in lowered:
        return "Masked "
    return ""


def _operand_kind(op: dict[str, str]) -> str:
    kind = op.get("type", "").strip().lower()
    if kind == "reg":
        return "register"
    if kind == "mem":
        return "memory"
    if kind == "imm":
        return "immediate"
    if kind == "agen":
        return "address"
    if kind == "flags":
        return "flags"
    return kind or "operand"


def _element_type_phrase(xtype: str, width: str) -> str:
    xtype = xtype.strip().lower()
    mapping = {
        "f16": "FP16",
        "f32": "single-precision floating-point",
        "f64": "double-precision floating-point",
        "i1": "mask",
        "i8": "8-bit integer",
        "u8": "8-bit integer",
        "i16": "16-bit integer",
        "u16": "16-bit integer",
        "i32": "32-bit integer",
        "u32": "32-bit integer",
        "i64": "64-bit integer",
        "u64": "64-bit integer",
        "i128": "128-bit integer",
        "u128": "128-bit integer",
    }
    if xtype in mapping:
        return mapping[xtype]
    if width.isdigit() and xtype.startswith(("i", "u")):
        return f"{width}-bit integer"
    return ""


def _shared_operand_phrase(operand_details: list[dict[str, str]]) -> str:
    semantic_ops = [op for op in operand_details if _operand_kind(op) in {"register", "memory", "immediate"}]
    if not semantic_ops:
        return "operands"
    kinds = {_operand_kind(op) for op in semantic_ops}
    xtypes = {op.get("xtype", "").strip().lower() for op in semantic_ops if op.get("xtype", "").strip().lower() and op.get("xtype", "").strip().lower() != "i1"}
    widths = {op.get("width", "").strip() for op in semantic_ops if op.get("width", "").strip()}
    if len(xtypes) == 1:
        phrase = _element_type_phrase(next(iter(xtypes)), next(iter(widths), ""))
        if phrase:
            return "mask operands" if phrase == "mask" else f"{phrase} operands"
    if len(widths) == 1:
        width = next(iter(widths))
        if width.isdigit():
            return f"{width}-bit operands"
    if kinds == {"register"}:
        return "register operands"
    if kinds == {"memory"}:
        return "memory operands"
    if kinds == {"immediate"}:
        return "immediate operands"
    if kinds == {"register", "memory"}:
        return "register and memory operands"
    if kinds == {"register", "immediate"}:
        return "register and immediate operands"
    if kinds == {"memory", "immediate"}:
        return "memory and immediate operands"
    if kinds == {"register", "memory", "immediate"}:
        return "register, memory, and immediate operands"
    return "operands"


def _verb_for_mnemonic(mnemonic: str) -> str:
    core = mnemonic.upper()
    for suffix in ("_ER_Z", "_ER", "_Z"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    if core.startswith("V") and len(core) > 3:
        core = core[1:]
    for key, verb in [
        ("ADC", "Add with carry"), ("ADD", "Add"), ("SUB", "Subtract"), ("SBB", "Subtract with borrow"),
        ("MUL", "Multiply"), ("IMUL", "Multiply"), ("DIV", "Divide"), ("IDIV", "Divide"),
        ("MOV", "Move"), ("CMP", "Compare"), ("AND", "Bitwise AND"), ("OR", "Bitwise OR"),
        ("XOR", "Bitwise XOR"), ("TEST", "Test"), ("MIN", "Compute minimum of"), ("MAX", "Compute maximum of"),
        ("BLEND", "Blend"), ("EXPAND", "Expand"), ("LOAD", "Load"), ("STORE", "Store"),
        ("SHUFFLE", "Shuffle"), ("PERM", "Permute"),
    ]:
        if core.startswith(key):
            return verb
    return core.replace("_", " ").title()


def _generated_instruction_summary(mnemonic: str, operand_details: list[dict[str, str]]) -> str:
    return f"{_verb_for_mnemonic(mnemonic)} {_shared_operand_phrase(operand_details)}".strip()


def _instruction_summary(mnemonic: str, raw_summary: str, operand_details: list[dict[str, str]]) -> str:
    base = raw_summary.strip().strip(".")
    prefix = _summary_prefix(mnemonic, operand_details, base)
    if not _summary_too_terse(base, mnemonic):
        return f"{prefix}{base}".strip() + "."
    return f"{prefix}{_generated_instruction_summary(mnemonic, operand_details)}.".strip()


def parse_intel_payload(text: str) -> list[IntrinsicRecord]:
    stripped = text.strip()
    if stripped.startswith("var data_js"):
        match = re.search(r'var\s+data_js\s*=\s*"(?P<body>.*)";\s*$', stripped, re.DOTALL)
        if not match:
            raise ValueError("could not locate Intel XML payload in data.js")
        xml_blob = match.group("body").replace("\\\n", "")
        stripped = bytes(xml_blob, "utf-8").decode("unicode_escape").strip()
    if stripped.startswith("<?xml") or stripped.startswith("<intrinsics_list"):
        records: list[IntrinsicRecord] = []
        for node in _iter_xml_elements(stripped, "intrinsic"):
            name = node.attrib.get("name", "").strip()
            if not name:
                continue
            return_node = node.find("./return")
            ret = node.attrib.get("rettype", "").strip() or (return_node.attrib.get("type", "").strip() if return_node is not None else "") or "void"
            params = []
            instruction_refs: list[dict[str, str]] = []
            for param in node.findall("./parameter"):
                ptype = param.attrib.get("type", "").strip()
                pname = param.attrib.get("varname", "").strip()
                params.append(" ".join(part for part in [ptype, pname] if part))
            description = " ".join((part.text or "").strip() for part in node.findall("./description") if (part.text or "").strip())
            cpuid = [cpuid.text.strip() for cpuid in node.findall("./CPUID") if (cpuid.text or "").strip()]
            notes: list[str] = []
            if node.attrib.get("sequence"):
                notes.append(node.attrib["sequence"].strip())
            for key in ("sequence", "sequence_note"):
                child = node.find(f"./{key}")
                if child is not None and (child.text or "").strip():
                    notes.append(child.text.strip())
            instructions: list[str] = []
            for inst in node.findall("./instruction"):
                inst_name = inst.attrib.get("name", "").strip()
                inst_form = inst.attrib.get("form", "").strip()
                inst_xed = inst.attrib.get("xed", "").strip()
                if not inst_name:
                    continue
                instruction_refs.append({"name": inst_name, "form": inst_form, "xed": inst_xed})
                instructions.append(_canonical_instruction_key(inst_name, inst_form) or inst_name)
            records.append(
                IntrinsicRecord(
                    name=name,
                    signature=f"{ret} {name}({', '.join(params)})",
                    description=description,
                    header=((node.findtext("./header") or "").strip() or node.attrib.get("header", "")),
                    isa=_normalize_isa(cpuid or node.attrib.get("isa", "") or node.attrib.get("tech", "")),
                    category=((node.findtext("./category") or "").strip() or node.attrib.get("category", "")),
                    subcategory=node.attrib.get("tech", "").strip(),
                    instructions=instructions,
                    instruction_refs=instruction_refs,
                    notes=notes,
                    aliases=[],
                )
            )
        return records

    json_blob = stripped
    if stripped.startswith("var ") or stripped.startswith("window."):
        match = re.search(r"(\{.*\}|\[.*\])", stripped, re.DOTALL)
        if not match:
            raise ValueError("could not locate JSON payload in Intel data")
        json_blob = match.group(1)

    payload = json.loads(json_blob)
    candidates = payload.get("intrinsics") or payload.get("data") or payload.get("records") or [] if isinstance(payload, dict) else payload
    records: list[IntrinsicRecord] = []
    for item in candidates:
        name = str(item.get("name") or item.get("intrinsic") or "").strip()
        if not name:
            continue
        signature = str(item.get("signature") or item.get("prototype") or "").strip()
        if not signature:
            return_type = str(item.get("returnType") or item.get("rettype") or "void").strip()
            params = item.get("parameters") or item.get("params") or []
            rendered_params = []
            if isinstance(params, list):
                for param in params:
                    if isinstance(param, dict):
                        ptype = str(param.get("type", "")).strip()
                        pname = str(param.get("name") or param.get("varname") or "").strip()
                        rendered_params.append(" ".join(part for part in [ptype, pname] if part))
                    else:
                        rendered_params.append(str(param).strip())
            signature = f"{return_type} {name}({', '.join(p for p in rendered_params if p)})"
        instructions = item.get("instructions") or item.get("instruction") or item.get("Instruction") or []
        if isinstance(instructions, str):
            instructions = [instructions]
        notes = item.get("notes") or item.get("operationNotes") or []
        if isinstance(notes, str):
            notes = [notes]
        aliases = item.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        description = str(item.get("description") or item.get("summary") or item.get("technology", "")).strip()
        records.append(
            IntrinsicRecord(
                name=name,
                signature=signature,
                description=description,
                header=str(item.get("header") or item.get("include") or "").strip(),
                isa=_normalize_isa(item.get("isa") or item.get("tech") or item.get("instructionSet") or []),
                category=str(item.get("category") or "").strip(),
                subcategory=str(item.get("tech") or item.get("subcategory") or "").strip(),
                instructions=[str(value).strip() for value in instructions if str(value).strip()],
                instruction_refs=[{"name": str(value).strip(), "form": "", "xed": ""} for value in instructions if str(value).strip()],
                notes=[str(value).strip() for value in notes if str(value).strip()],
                aliases=[str(value).strip() for value in aliases if str(value).strip()],
            )
        )
    return records


def parse_uops_xml(source: str | Path) -> list[InstructionRecord]:
    records: list[InstructionRecord] = []
    for node in _iter_xml_elements(source, "instruction"):
        mnemonic = _intern_small((node.attrib.get("asm") or node.attrib.get("name") or "").strip())
        if not mnemonic:
            continue
        form = (node.attrib.get("string") or node.attrib.get("form") or node.attrib.get("cpl") or node.attrib.get("category") or "").strip()
        raw_summary = node.attrib.get("summary", "").strip()
        isa = _normalize_isa(node.attrib.get("isa-set", "") or node.attrib.get("extension", "") or node.attrib.get("isa", ""))
        operand_details: list[dict[str, str]] = []
        metadata = {
            key: (_intern_small(value.strip()) if key in {"category", "cpl", "extension", "iclass"} else value.strip())
            for key, value in node.attrib.items()
            if key in _UOPS_METADATA_KEYS
        }
        if raw_summary:
            metadata["uops_summary"] = raw_summary
        arch_details: dict[str, dict[str, Any]] = {}
        for child in node:
            if child.tag == "operand":
                xtype = _normalize_operand_xtype(child.attrib.get("xtype", "").strip())
                operand_payload = {
                    key: (_intern_small(value.strip()) if key in {"type", "width", "name"} else value.strip())
                    for key, value in child.attrib.items()
                    if key in _UOPS_OPERAND_KEYS
                }
                if xtype:
                    operand_payload["xtype"] = _intern_small(xtype)
                operand_details.append(operand_payload)
            elif child.tag == "architecture":
                arch = child.attrib.get("name") or child.attrib.get("uarch") or child.attrib.get("arch")
                if not arch:
                    continue
                arch = _intern_small(arch)
                arch_entry: dict[str, Any] = {"measurement": {}, "latencies": [], "doc": {}, "iaca": []}
                for grandchild in child:
                    if grandchild.tag == "measurement":
                        arch_entry["measurement"] = dict(grandchild.attrib)
                        for latency in grandchild.findall("./latency"):
                            arch_entry["latencies"].append(dict(latency.attrib))
                    elif grandchild.tag == "doc":
                        arch_entry["doc"] = dict(grandchild.attrib)
                    elif grandchild.tag == "IACA":
                        arch_entry["iaca"].append(dict(grandchild.attrib))
                arch_details[arch] = arch_entry
        records.append(
            InstructionRecord(
                mnemonic=mnemonic,
                form=form,
                summary=_instruction_summary(mnemonic, raw_summary, operand_details),
                isa=isa,
                operand_details=operand_details,
                metadata=metadata,
                arch_details=arch_details,
            )
        )
    return records


def link_records(intrinsics: list[IntrinsicRecord], instructions: list[InstructionRecord]) -> None:
    by_mnemonic: dict[str, list[InstructionRecord]] = {}
    by_key: dict[str, list[InstructionRecord]] = {}
    by_iform: dict[str, list[InstructionRecord]] = {}
    for record in instructions:
        by_mnemonic.setdefault(record.mnemonic.casefold(), []).append(record)
        by_key.setdefault(record.key.casefold(), []).append(record)
        if record.form:
            by_mnemonic.setdefault(record.key.casefold(), []).append(record)
        if record.metadata.get("iform"):
            by_iform.setdefault(record.metadata["iform"].casefold(), []).append(record)
    for intrinsic in intrinsics:
        linked: list[str] = []
        refs = intrinsic.instruction_refs or [{"name": name, "form": "", "xed": ""} for name in intrinsic.instructions]
        for ref in refs:
            matched: list[InstructionRecord] = []
            xed = ref.get("xed", "").strip()
            name = ref.get("name", "").strip()
            form = ref.get("form", "").strip()
            if xed:
                matched = by_iform.get(xed.casefold(), [])
            if not matched and name and form:
                matched = by_key.get(_canonical_instruction_key(name, form).casefold(), [])
            if not matched and name:
                matched = by_mnemonic.get(name.casefold(), [])
            if not matched:
                fallback = _canonical_instruction_key(name, form) or name
                if fallback:
                    linked.append(fallback)
                continue
            for instruction in matched:
                if intrinsic.name not in instruction.linked_intrinsics:
                    instruction.linked_intrinsics.append(intrinsic.name)
                linked.append(instruction.key)
        intrinsic.instructions = sorted(set(linked))


def build_catalog(
    offline: bool = False,
    include_sdm: bool = False,
    *,
    status: Callable[[str], None] | None = None,
) -> Catalog:
    emit = status or (lambda _msg: None)
    emit("Fetching Intel intrinsics data")
    intel_text, intel_source = fetch_intel_data(offline=offline)
    emit(f"Fetched Intel intrinsics data from {intel_source.url}")
    emit("Fetching uops.info instruction data")
    uops_text, uops_source = fetch_uops_xml(offline=offline)
    emit(f"Fetched uops.info instruction data from {uops_source.url}")
    emit("Parsing intrinsic catalog")
    intrinsics = parse_intel_payload(intel_text)
    emit(f"Parsed {len(intrinsics)} intrinsics")
    emit("Parsing instruction catalog")
    instructions = parse_uops_xml(uops_text)
    emit(f"Parsed {len(instructions)} instructions")
    emit("Linking intrinsics to instructions")
    link_records(intrinsics, instructions)
    emit("Linked intrinsics and instructions")

    if include_sdm:
        sdm_path = find_pdf_source_path("intel-sdm", offline=offline)
        if sdm_path is not None:
            try:
                emit(f"Preparing Intel SDM descriptions from {sdm_path}")
                result = load_or_parse_pdf_source("intel-sdm", sdm_path, status=status)
                merge_pdf_enrichment(instructions, "intel-sdm", result)
                emit("Merged Intel SDM descriptions into instruction records")
            except Exception:
                pass

    emit("Assembling final catalog")
    return Catalog(
        intrinsics=sorted(intrinsics, key=lambda item: item.name),
        instructions=sorted(instructions, key=lambda item: (item.mnemonic, item.form)),
        sources=[intel_source, uops_source],
        generated_at=now_iso(),
    )
