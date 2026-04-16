#!/usr/bin/env python3
"""Developer-only source validation for simdref ingestion."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from simdref.arm_instructions import parse_arm_instruction_payload
from simdref.ingest_catalog import (
    _arm_live_instruction_refs,
    _canonical_instruction_key,
    _iter_xml_elements,
    _normalize_arm_intrinsic_name,
    parse_arm_intrinsics_payload,
    parse_intel_payload,
    parse_uops_xml,
)
from simdref.ingest_sources import fetch_arm_a64_data, fetch_arm_acle_data, fetch_intel_data, fetch_uops_xml


def _fail(message: str) -> None:
    raise AssertionError(message)


def _strip(value: Any) -> str:
    return str(value or "").strip()


def _normalize_isa_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,/|]\s*|\s{2,}", value) if part.strip()]
    return []


def _unwrap_intel_source_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("var data_js"):
        match = re.search(r'var\s+data_js\s*=\s*"(?P<body>.*)";\s*$', stripped, re.DOTALL)
        if not match:
            _fail("could not locate Intel XML payload in data.js")
        xml_blob = match.group("body").replace("\\\n", "")
        return bytes(xml_blob, "utf-8").decode("unicode_escape").strip()
    if stripped.startswith("var ") or stripped.startswith("window."):
        match = re.search(r"(\{.*\}|\[.*\])", stripped, re.DOTALL)
        if not match:
            _fail("could not locate JSON payload in Intel source data")
        return match.group(1)
    return stripped


def _raw_intel_intrinsic_records(text: str) -> list[dict[str, Any]]:
    unwrapped = _unwrap_intel_source_text(text)
    if unwrapped.startswith("<?xml") or unwrapped.startswith("<intrinsics_list"):
        records: list[dict[str, Any]] = []
        for node in _iter_xml_elements(unwrapped, "intrinsic"):
            name = _strip(node.attrib.get("name"))
            if not name:
                continue
            return_node = node.find("./return")
            ret = (
                _strip(node.attrib.get("rettype"))
                or (_strip(return_node.attrib.get("type")) if return_node is not None else "")
                or "void"
            )
            params = []
            for param in node.findall("./parameter"):
                ptype = _strip(param.attrib.get("type"))
                pname = _strip(param.attrib.get("varname"))
                params.append(" ".join(part for part in [ptype, pname] if part))
            instructions = []
            for inst in node.findall("./instruction"):
                inst_name = _strip(inst.attrib.get("name"))
                inst_form = _strip(inst.attrib.get("form"))
                if not inst_name:
                    continue
                instructions.append(_canonical_instruction_key(inst_name, inst_form) or inst_name)
            records.append(
                {
                    "name": name,
                    "signature": f"{ret} {name}({', '.join(params)})",
                    "header": _strip(node.findtext("./header") or node.attrib.get("header")),
                    "isa": [_strip(cpuid.text) for cpuid in node.findall("./CPUID") if _strip(cpuid.text)],
                    "category": _strip(node.findtext("./category") or node.attrib.get("category")),
                    "instructions": instructions,
                }
            )
        return records

    payload = json.loads(unwrapped)
    candidates = payload.get("intrinsics") or payload.get("data") or payload.get("records") or [] if isinstance(payload, dict) else payload
    records = []
    for item in candidates or []:
        name = _strip(item.get("name") or item.get("intrinsic"))
        if not name:
            continue
        signature = _strip(item.get("signature") or item.get("prototype"))
        if not signature:
            return_type = _strip(item.get("returnType") or item.get("rettype") or "void")
            params = item.get("parameters") or item.get("params") or []
            rendered_params = []
            if isinstance(params, list):
                for param in params:
                    if isinstance(param, dict):
                        ptype = _strip(param.get("type"))
                        pname = _strip(param.get("name") or param.get("varname"))
                        rendered_params.append(" ".join(part for part in [ptype, pname] if part))
                    else:
                        rendered_params.append(_strip(param))
            signature = f"{return_type} {name}({', '.join(p for p in rendered_params if p)})"
        instructions = item.get("instructions") or item.get("instruction") or item.get("Instruction") or []
        if isinstance(instructions, str):
            instructions = [instructions]
        records.append(
            {
                "name": name,
                "signature": signature,
                "header": _strip(item.get("header") or item.get("include")),
                "isa": _normalize_isa_list(item.get("isa") or item.get("tech") or item.get("instructionSet") or []),
                "category": _strip(item.get("category")),
                "instructions": [_strip(value) for value in instructions if _strip(value)],
            }
        )
    return records


def validate_intel_intrinsics(offline: bool) -> tuple[int, int]:
    text, _source = fetch_intel_data(offline=offline)
    parsed = parse_intel_payload(text)
    parsed_by_name = {record.name: record for record in parsed}
    raw_records = _raw_intel_intrinsic_records(text)
    failures = 0
    checked = 0

    for raw in raw_records:
        name = raw["name"]
        checked += 1
        record = parsed_by_name.get(name)
        if record is None:
            print(f"FAIL intel intrinsics: missing {name}")
            failures += 1
            continue
        if f"{name}(" not in record.signature:
            print(f"FAIL intel intrinsics: signature missing intrinsic name for {name}")
            failures += 1
        if raw["header"] and record.header != raw["header"]:
            print(f"FAIL intel intrinsics: header mismatch for {name}")
            failures += 1
        if raw["category"] and record.category != raw["category"]:
            print(f"FAIL intel intrinsics: category mismatch for {name}")
            failures += 1
        if raw["instructions"]:
            parsed_insts = [str(value).strip() for value in record.instructions if str(value).strip()]
            if not parsed_insts:
                print(f"FAIL intel intrinsics: missing parsed instruction refs for {name}")
                failures += 1
        if raw["isa"] and not record.isa:
            print(f"FAIL intel intrinsics: missing isa tags for {name}")
            failures += 1

    if len(parsed) < len(raw_records):
        print(f"FAIL intel intrinsics: parsed count {len(parsed)} < source count {len(raw_records)}")
        failures += 1
    print(f"validated Intel intrinsics: checked={checked} parsed={len(parsed)} failures={failures}")
    return checked, failures


def _raw_uops_instructions(source: str | Path) -> list[dict[str, Any]]:
    raw_records: list[dict[str, Any]] = []
    for node in _iter_xml_elements(source, "instruction"):
        mnemonic = _strip(node.attrib.get("asm") or node.attrib.get("name"))
        if not mnemonic:
            continue
        form = _strip(node.attrib.get("string") or node.attrib.get("form") or node.attrib.get("cpl") or node.attrib.get("category"))
        raw_records.append(
            {
                "mnemonic": mnemonic,
                "form": form,
                "iform": _strip(node.attrib.get("iform")),
                "iclass": _strip(node.attrib.get("iclass")),
                "extension": _strip(node.attrib.get("extension")),
                "category": _strip(node.attrib.get("category")),
                "url": _strip(node.attrib.get("url")),
                "url-ref": _strip(node.attrib.get("url-ref")),
                "operand_count": len(node.findall("./operand")),
            }
        )
    return raw_records


def validate_uops_instructions(offline: bool) -> tuple[int, int]:
    source, _source = fetch_uops_xml(offline=offline)
    parsed = parse_uops_xml(source)
    by_iform: dict[str, list[Any]] = {}
    by_key: dict[tuple[str, str], list[Any]] = {}
    for record in parsed:
        if record.metadata.get("iform"):
            by_iform.setdefault(record.metadata["iform"], []).append(record)
        by_key.setdefault((record.mnemonic, record.form), []).append(record)
    raw_records = _raw_uops_instructions(source)
    failures = 0
    checked = 0

    for raw in raw_records:
        checked += 1
        candidates = by_iform.get(raw["iform"], []) if raw["iform"] else []
        if not candidates:
            candidates = by_key.get((raw["mnemonic"], raw["form"]), [])
        if not candidates:
            print(f"FAIL uops: missing record for {raw['mnemonic']} / {raw['iform'] or raw['form']}")
            failures += 1
            continue

        record = None
        for candidate in candidates:
            if candidate.mnemonic != raw["mnemonic"]:
                continue
            if raw["form"] and candidate.form != raw["form"]:
                continue
            if len(candidate.operand_details) != raw["operand_count"]:
                continue
            record = candidate
            break
        if record is None:
            record = candidates[0]

        if record.mnemonic != raw["mnemonic"]:
            print(f"FAIL uops: mnemonic mismatch for {raw['iform'] or raw['mnemonic']}")
            failures += 1
        if raw["iform"] and record.metadata.get("iform", "") != raw["iform"]:
            print(f"FAIL uops: iform mismatch for {raw['iform']}")
            failures += 1
        if raw["iclass"] and record.metadata.get("iclass", "") != raw["iclass"]:
            print(f"FAIL uops: iclass mismatch for {raw['iform'] or raw['mnemonic']}")
            failures += 1
        if raw["category"] and record.metadata.get("category", "") != raw["category"]:
            print(f"FAIL uops: category mismatch for {raw['iform'] or raw['mnemonic']}")
            failures += 1
        if raw["extension"] and record.metadata.get("extension", "") != raw["extension"]:
            print(f"FAIL uops: extension mismatch for {raw['iform'] or raw['mnemonic']}")
            failures += 1
        if len(record.operand_details) != raw["operand_count"]:
            print(f"FAIL uops: operand count mismatch for {raw['iform'] or raw['mnemonic']}")
            failures += 1
        if not record.summary.strip():
            print(f"FAIL uops: empty summary for {raw['iform'] or raw['mnemonic']}")
            failures += 1

    if len(parsed) < len(raw_records):
        print(f"FAIL uops: parsed count {len(parsed)} < source count {len(raw_records)}")
        failures += 1
    print(f"validated uops.info instructions: checked={checked} parsed={len(parsed)} failures={failures}")
    return checked, failures


def validate_arm_intrinsics(offline: bool) -> tuple[int, int]:
    text, _source = fetch_arm_acle_data(offline=offline)
    parsed = parse_arm_intrinsics_payload(text)
    parsed_by_name = {record.name: record for record in parsed}
    payload = json.loads(text)
    failures = 0
    checked = 0

    if payload.get("format") != "arm-intrinsics-json-v1":
        print(f"validated Arm intrinsics: checked={len(parsed)} parsed={len(parsed)} failures=0 (fallback payload)")
        return len(parsed), 0

    raw_intrinsics = json.loads(str(payload.get("intrinsics_json") or "[]"))
    operations_payload = json.loads(str(payload.get("operations_json") or "[]"))
    operations = {
        str(item.get("item", {}).get("id") or ""): item.get("item", {})
        for item in operations_payload
        if isinstance(item, dict) and item.get("item")
    }

    for item in raw_intrinsics:
        simd_isa = [str(value).strip() for value in item.get("SIMD_ISA") or [] if str(value).strip()]
        if not {"Neon", "SVE", "SVE2"} & set(simd_isa):
            continue
        raw_name = _strip(item.get("name"))
        if not raw_name:
            continue
        checked += 1
        name = _normalize_arm_intrinsic_name(raw_name)
        record = parsed_by_name.get(name)
        if record is None:
            print(f"FAIL arm intrinsics: missing {name}")
            failures += 1
            continue
        expected_header = "arm_neon.h" if "Neon" in simd_isa else "arm_sve.h"
        if record.header != expected_header:
            print(f"FAIL arm intrinsics: header mismatch for {name}")
            failures += 1
        expected_refs = _arm_live_instruction_refs([group for group in (item.get("instructions") or []) if isinstance(group, dict)])
        if record.instruction_refs != expected_refs:
            print(f"FAIL arm intrinsics: instruction refs mismatch for {name}")
            failures += 1
        expected_arches = "/".join(str(value).strip() for value in item.get("Architectures") or [] if str(value).strip())
        if record.metadata.get("supported_architectures", "") != expected_arches:
            print(f"FAIL arm intrinsics: supported architectures mismatch for {name}")
            failures += 1
        operation_id = _strip(item.get("Operation"))
        operation_content = _strip((operations.get(operation_id) or {}).get("content"))
        if operation_content and "ACLE Operation" not in record.doc_sections:
            print(f"FAIL arm intrinsics: missing ACLE Operation for {name}")
            failures += 1

    print(f"validated Arm intrinsics: checked={checked} parsed={len(parsed)} failures={failures}")
    return checked, failures


def _instruction_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("instructions", "base_instructions", "instruction_set", "InstructionSet", "items", "records"):
        value = payload.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    for value in payload.values():
        if isinstance(value, dict):
            nested = _instruction_candidates(value)
            if nested:
                return nested
    return []


def validate_arm_instructions(offline: bool, require_authoritative: bool) -> tuple[int, int]:
    text, source = fetch_arm_a64_data(offline=offline)
    if source.used_fixture and require_authoritative:
        _fail("authoritative Arm instruction source is required, but only the bundled fixture is available")

    payload = json.loads(text)
    parsed = parse_arm_instruction_payload(text)
    parsed_by_mnemonic: dict[str, list[Any]] = {}
    for record in parsed:
        parsed_by_mnemonic.setdefault(record.mnemonic, []).append(record)

    if isinstance(payload, dict) and payload.get("format") == "arm-aarchmrs-instructions-v1":
        raw_payload = json.loads(str(payload.get("instructions_json") or "[]"))
    elif isinstance(payload, dict) and payload.get("format") == "arm-instructions-fixture-v1":
        raw_payload = payload.get("instructions") or []
    else:
        raw_payload = payload

    raw_records = _instruction_candidates(raw_payload)
    failures = 0
    checked = 0
    for item in raw_records:
        mnemonic = _strip(
            item.get("mnemonic")
            or item.get("base_instruction")
            or item.get("instruction")
            or item.get("name")
            or item.get("opcode")
        ).upper()
        if not mnemonic:
            continue
        checked += 1
        candidates = parsed_by_mnemonic.get(mnemonic, [])
        if not candidates:
            print(f"FAIL arm instructions: missing {mnemonic}")
            failures += 1
            continue
        raw_url = _strip(item.get("url") or item.get("source_url") or item.get("reference_url"))
        match = None
        for candidate in candidates:
            if raw_url and candidate.metadata.get("url") == raw_url:
                match = candidate
                break
        if match is None:
            match = candidates[0]
        if raw_url and match.metadata.get("url") != raw_url:
            print(f"FAIL arm instructions: url mismatch for {mnemonic}")
            failures += 1
        if not match.summary.strip():
            print(f"FAIL arm instructions: empty summary for {mnemonic}")
            failures += 1

    suffix = "authoritative" if not source.used_fixture else "fixture-only"
    print(f"validated Arm instructions: checked={checked} parsed={len(parsed)} failures={failures} ({suffix})")
    return checked, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Developer-only source validation for simdref")
    parser.add_argument("--offline", action="store_true", help="Validate bundled fixture sources only.")
    parser.add_argument(
        "--require-authoritative-arm-instructions",
        action="store_true",
        help="Fail if the Arm instruction source falls back to the bundled fixture.",
    )
    args = parser.parse_args()

    totals_checked = 0
    totals_failed = 0

    for checked, failed in (
        validate_intel_intrinsics(args.offline),
        validate_uops_instructions(args.offline),
        validate_arm_intrinsics(args.offline),
        validate_arm_instructions(args.offline, args.require_authoritative_arm_instructions),
    ):
        totals_checked += checked
        totals_failed += failed

    if totals_failed:
        print(f"source validation failed: checked={totals_checked} failures={totals_failed}")
        return 1
    print(f"source validation passed: checked={totals_checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
