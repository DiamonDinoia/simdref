"""Data ingestion pipeline for simdref.

Fetches Intel Intrinsics Guide data and uops.info instruction XML, parses
them into :class:`~simdref.models.IntrinsicRecord` and
:class:`~simdref.models.InstructionRecord` instances, links them
bidirectionally, and assembles the complete :class:`~simdref.models.Catalog`.

Supports multiple fallback sources: upstream CDNs, local vendor archives,
and bundled fixture files for fully offline operation.
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from simdref.models import Catalog, InstructionRecord, IntrinsicRecord, SourceVersion
from simdref.pdfparse.intel import INTEL_SDM_URL, parse_intel_sdm

UOPS_XML_URL = "https://uops.info/instructions.xml"
INTEL_OFFLINE_ZIP_URL = "https://cdrdv2.intel.com/v1/dl/getContent/764289?fileName=Intel-Intrinsics-Guide-Offline-3.6.4.zip"
INTEL_INDEX_URL = "https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html"
INTEL_CANDIDATE_DATA_URLS = [
    "https://www.intel.com/content/dam/develop/public/us/en/include/intrinsics-guide/files/data.js",
    "https://www.intel.com/content/dam/develop/public/us/en/include/intrinsics-guide/files/intrinsics.json",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_INTEL_ARCHIVES = [
    _REPO_ROOT / "vendor" / "intel" / "Intel-Intrinsics-Guide-Offline-3.6.9.zip",
]
LOCAL_UOPS_XMLS = [
    _REPO_ROOT / "vendor" / "uops" / "instructions.xml",
]
LOCAL_INTEL_SDM_PDFS = [
    _REPO_ROOT / "vendor" / "intel" / "intel-sdm.pdf",
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _fixture_text(name: str) -> str:
    return resources.files("simdref.fixtures").joinpath(name).read_text()


def _fetch_text(url: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=20.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _read_local_intel_archive() -> tuple[str, SourceVersion] | None:
    for archive_path in LOCAL_INTEL_ARCHIVES:
        if not archive_path.exists():
            continue
        with zipfile.ZipFile(archive_path) as zf:
            text = zf.read("Intel Intrinsics Guide/files/data.js").decode("utf-8", "replace")
        return text, SourceVersion(
            source="intel-intrinsics-guide",
            version=f"offline-package:{archive_path.stem}",
            fetched_at=now_iso(),
            url=str(archive_path),
        )
    return None


def fetch_uops_xml(offline: bool = False) -> tuple[str, SourceVersion]:
    if offline:
        return _fixture_text("uops_sample.xml"), SourceVersion(
            source="uops.info",
            version="fixture",
            fetched_at=now_iso(),
            url="fixture:uops_sample.xml",
            used_fixture=True,
        )
    for xml_path in LOCAL_UOPS_XMLS:
        if xml_path.exists():
            return xml_path.read_text(), SourceVersion(
                source="uops.info",
                version=f"local-xml:{xml_path.name}",
                fetched_at=now_iso(),
                url=str(xml_path),
            )
    try:
        text = _fetch_text(UOPS_XML_URL)
        return text, SourceVersion(
            source="uops.info",
            version="live",
            fetched_at=now_iso(),
            url=UOPS_XML_URL,
        )
    except Exception:
        return fetch_uops_xml(offline=True)


def fetch_intel_data(offline: bool = False) -> tuple[str, SourceVersion]:
    if offline:
        return _fixture_text("intel_intrinsics_sample.json"), SourceVersion(
            source="intel-intrinsics-guide",
            version="fixture",
            fetched_at=now_iso(),
            url="fixture:intel_intrinsics_sample.json",
            used_fixture=True,
        )

    local_archive = _read_local_intel_archive()
    if local_archive is not None:
        return local_archive

    last_error: Exception | None = None
    for url in INTEL_CANDIDATE_DATA_URLS:
        try:
            text = _fetch_text(url)
            return text, SourceVersion(
                source="intel-intrinsics-guide",
                version="live",
                fetched_at=now_iso(),
                url=url,
            )
        except Exception as exc:
            last_error = exc
    try:
        html = _fetch_text(INTEL_INDEX_URL)
        matches = re.findall(r'files/[^"\']+\.(?:js|json|xml)', html)
        for match in matches:
            candidate = f"https://www.intel.com/content/dam/develop/public/us/en/include/intrinsics-guide/{match}"
            try:
                text = _fetch_text(candidate)
                return text, SourceVersion(
                    source="intel-intrinsics-guide",
                    version="live",
                    fetched_at=now_iso(),
                    url=candidate,
                )
            except Exception as exc:
                last_error = exc
    except Exception as exc:
        last_error = exc

    # Fallback: download the official offline zip package
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            resp = client.get(INTEL_OFFLINE_ZIP_URL)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for name in zf.namelist():
                    if name.endswith("data.js") or name.endswith("data.json"):
                        text = zf.read(name).decode("utf-8", "replace")
                        return text, SourceVersion(
                            source="intel-intrinsics-guide",
                            version="offline-zip-download",
                            fetched_at=now_iso(),
                            url=INTEL_OFFLINE_ZIP_URL,
                        )
    except Exception as exc:
        last_error = exc

    return fetch_intel_data(offline=True)


def _find_intel_sdm_pdf(offline: bool = False) -> Path | None:
    """Locate or download the Intel SDM PDF. Returns path or None."""
    if offline:
        return None
    for pdf_path in LOCAL_INTEL_SDM_PDFS:
        if pdf_path.exists():
            return pdf_path
    # Try downloading with progress bar
    try:
        from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn

        dest = LOCAL_INTEL_SDM_PDFS[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(follow_redirects=True, timeout=120.0) as client:
            with client.stream("GET", INTEL_SDM_URL) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                with Progress(
                    "[progress.description]{task.description}",
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                ) as progress:
                    task = progress.add_task("Downloading Intel SDM PDF", total=total or None)
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_bytes(65536):
                            f.write(chunk)
                            progress.advance(task, len(chunk))
        return dest
    except Exception:
        return None


def _merge_descriptions(
    instructions: list[InstructionRecord],
    descriptions: dict[str, dict[str, object]],
) -> None:
    """Merge parsed PDF descriptions into instruction records in-place.

    Tries exact mnemonic match first, then falls back to stripping a
    leading ``V`` prefix (Intel SDM lists "ADDPD/VADDPD" as a single
    entry, so VADDPD should inherit ADDPD's description if no separate
    entry exists).
    """
    # Build a list of candidate base mnemonics from a decorated mnemonic.
    # Strips prefixes like {EVEX}, {NF}, {LOAD}, LOCK, REP, etc.
    # and tries element-type suffix substitutions (PH->PD, BF16->PS, etc.)
    _TYPE_SUFFIX_MAP = [
        ("PH", "PD"), ("PH", "PS"),
        ("BF16", "PS"), ("BF8", "PS"), ("BF8S", "PS"),
        ("HF8", "PS"), ("HF8S", "PS"),
        ("IBS", "DQ"), ("IUBS", "UDQ"),
    ]

    def _strip_prefix(mnemonic: str) -> str:
        """Strip decoration prefixes, return bare mnemonic."""
        m = mnemonic
        while m.startswith("{"):
            end = m.find("}")
            if end == -1:
                break
            m = m[end + 1:].lstrip()
        for prefix in ("LOCK ", "REPE ", "REPNE ", "REP ", "REX64 "):
            if m.startswith(prefix):
                m = m[len(prefix):]
                break
        return m

    # Data-type suffixes that map individual variants back to PDF group keys.
    # Ordered longest-first so e.g. "F32X4" is tried before "F32" or "4".
    _GROUP_SUFFIXES = [
        # Broadcast/insert/extract format specifiers
        "F32X8", "F32X4", "F32X2", "F64X4", "F64X2", "F128",
        "I32X8", "I32X4", "I32X2", "I64X4", "I64X2", "I128",
        # Mask broadcast
        "MB2Q", "MW2D",
        # Sign/zero-extend type pairs
        "BD", "BW", "BQ", "DQ", "WD", "WQ",
        # Scalar/packed float types
        "SD", "SS", "PD", "PS",
        # Element width single-letter
        "B", "W", "D", "Q",
        # Bit widths
        "64", "32", "16", "8",
    ]
    _MIN_GROUP_KEY_LEN = 5

    def _base_candidates(mnemonic: str) -> list[str]:
        candidates: list[str] = []
        bare = _strip_prefix(mnemonic)
        if bare != mnemonic:
            candidates.append(bare)
        # V prefix (VADDPD -> ADDPD)
        if bare.startswith("V") and len(bare) > 1:
            candidates.append(bare[1:])
        # Combined: strip prefix + V prefix
        for c in list(candidates):
            if c.startswith("V") and len(c) > 1 and c[1:] not in candidates:
                candidates.append(c[1:])
        # Element-type suffix substitutions (for newer ISA variants)
        all_forms = [mnemonic, bare] + candidates
        for form in list(all_forms):
            # Trailing S (saturating variant, e.g. VCVTTPD2DQS -> VCVTTPD2DQ)
            if form.endswith("S") and len(form) > 3:
                candidates.append(form[:-1])
            for old_suffix, new_suffix in _TYPE_SUFFIX_MAP:
                if form.endswith(old_suffix):
                    candidates.append(form[: -len(old_suffix)] + new_suffix)
        # Group-key suffix stripping (PMOVSXBD -> PMOVSX, VBROADCASTSD -> VBROADCAST)
        for form in list(all_forms):
            for suffix in _GROUP_SUFFIXES:
                if form.endswith(suffix):
                    stem = form[: -len(suffix)]
                    if len(stem) >= _MIN_GROUP_KEY_LEN and stem not in candidates:
                        candidates.append(stem)
        return candidates

    for record in instructions:
        mnemonic = record.mnemonic.upper()
        desc = descriptions.get(mnemonic)
        if desc is None:
            for candidate in _base_candidates(mnemonic):
                desc = descriptions.get(candidate)
                if desc is not None:
                    break
        if desc is not None:
            record.description = dict(desc.get("sections") or {})
            page_start = desc.get("page_start")
            page_end = desc.get("page_end")
            if page_start:
                record.metadata["intel-sdm-page-start"] = str(page_start)
                record.metadata["intel-sdm-url"] = f"{INTEL_SDM_URL}#page={page_start}"
            if page_end:
                record.metadata["intel-sdm-page-end"] = str(page_end)


def _normalize_isa(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        parts = re.split(r"[,/|]\s*|\s{2,}", value)
        return [part.strip() for part in parts if part.strip()]
    return []


def _canonical_instruction_key(name: str, form: str) -> str:
    instruction_name = name.strip().upper()
    instruction_form = form.strip()
    if not instruction_name:
        return ""
    if not instruction_form:
        return instruction_name
    return f"{instruction_name} ({instruction_form.upper()})"


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


def _bit_width_phrase(width: str) -> str:
    width = width.strip()
    return f"{width}-bit " if width.isdigit() else ""


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
        xtype = next(iter(xtypes))
        sample_width = next(iter(widths), "")
        phrase = _element_type_phrase(xtype, sample_width)
        if phrase:
            if phrase == "mask":
                return "mask operands"
            return f"{phrase} operands"
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
    for prefix in ("V",):
        if core.startswith(prefix) and len(core) > 3:
            core = core[1:]
            break
    verb_map = [
        ("ADC", "Add with carry"),
        ("ADD", "Add"),
        ("SUB", "Subtract"),
        ("SBB", "Subtract with borrow"),
        ("MUL", "Multiply"),
        ("IMUL", "Multiply"),
        ("DIV", "Divide"),
        ("IDIV", "Divide"),
        ("MOV", "Move"),
        ("CMP", "Compare"),
        ("AND", "Bitwise AND"),
        ("OR", "Bitwise OR"),
        ("XOR", "Bitwise XOR"),
        ("TEST", "Test"),
        ("MIN", "Compute minimum of"),
        ("MAX", "Compute maximum of"),
        ("BLEND", "Blend"),
        ("EXPAND", "Expand"),
        ("LOAD", "Load"),
        ("STORE", "Store"),
        ("SHUFFLE", "Shuffle"),
        ("PERM", "Permute"),
    ]
    for key, verb in verb_map:
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
        root = ET.fromstring(stripped)
        records: list[IntrinsicRecord] = []
        for node in root.findall(".//intrinsic"):
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
    if isinstance(payload, dict):
        candidates = payload.get("intrinsics") or payload.get("data") or payload.get("records") or []
    else:
        candidates = payload

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


def parse_uops_xml(text: str) -> list[InstructionRecord]:
    root = ET.fromstring(text)
    records: list[InstructionRecord] = []
    for node in root.findall(".//instruction"):
        mnemonic = node.attrib.get("asm") or node.attrib.get("name") or ""
        mnemonic = mnemonic.strip()
        if not mnemonic:
            continue
        form = (
            node.attrib.get("string")
            or node.attrib.get("form")
            or node.attrib.get("cpl")
            or node.attrib.get("category")
            or ""
        ).strip()
        raw_summary = node.attrib.get("summary", "").strip()
        isa = _normalize_isa(node.attrib.get("isa-set", "") or node.attrib.get("extension", "") or node.attrib.get("isa", ""))
        operands: list[str] = []
        operand_details: list[dict[str, str]] = []
        metadata = {
            key: value
            for key, value in node.attrib.items()
            if key not in {"string", "summary"}
        }
        if raw_summary:
            metadata["uops_summary"] = raw_summary
        metrics: dict[str, dict[str, str]] = {}
        arch_details: dict[str, dict[str, Any]] = {}
        for child in node:
            tag = child.tag.lower()
            if tag == "operand":
                rendered_rw = "".join(flag for flag in ("r", "w") if child.attrib.get(flag) == "1")
                xtype = _normalize_operand_xtype(child.attrib.get("xtype", "").strip())
                text_parts = [
                    f"idx={child.attrib.get('idx', '').strip()}",
                    rendered_rw,
                    child.attrib.get("type", "").strip(),
                    child.attrib.get("memory-prefix", "").strip(),
                    child.attrib.get("width", "").strip(),
                    xtype,
                    child.attrib.get("name", "").strip(),
                    (child.text or "").strip(),
                ]
                rendered = " ".join(part for part in text_parts if part)
                if rendered:
                    operands.append(rendered)
                operand_payload = {key: value for key, value in child.attrib.items()}
                operand_payload["xtype"] = xtype
                operand_details.append(operand_payload | {"values": (child.text or "").strip()})
            if tag in {"architecture", "measurement", "doc"}:
                if tag == "architecture":
                    arch = child.attrib.get("name") or child.attrib.get("uarch") or child.attrib.get("arch")
                    if not arch:
                        continue
                    arch_entry: dict[str, Any] = {"measurement": {}, "latencies": [], "doc": {}, "iaca": []}
                    for grandchild in child:
                        grand_tag = grandchild.tag
                        if grand_tag == "measurement":
                            arch_entry["measurement"] = dict(grandchild.attrib)
                            for latency in grandchild.findall("./latency"):
                                arch_entry["latencies"].append(dict(latency.attrib))
                            if arch_entry["measurement"]:
                                metrics[arch] = dict(arch_entry["measurement"])
                        elif grand_tag == "doc":
                            arch_entry["doc"] = dict(grandchild.attrib)
                        elif grand_tag == "IACA":
                            arch_entry["iaca"].append(dict(grandchild.attrib))
                    arch_details[arch] = arch_entry
        summary = _instruction_summary(mnemonic, raw_summary, operand_details)
        records.append(
            InstructionRecord(
                mnemonic=mnemonic,
                form=form,
                summary=summary,
                isa=isa,
                operands=operands,
                operand_details=operand_details,
                metadata=metadata,
                arch_details=arch_details,
                metrics=metrics,
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


def build_catalog(offline: bool = False, include_sdm: bool = False) -> Catalog:
    intel_text, intel_source = fetch_intel_data(offline=offline)
    uops_text, uops_source = fetch_uops_xml(offline=offline)
    intrinsics = parse_intel_payload(intel_text)
    instructions = parse_uops_xml(uops_text)
    link_records(intrinsics, instructions)

    # Parse Intel SDM PDF for rich descriptions only when explicitly requested.
    sdm_path = _find_intel_sdm_pdf(offline=offline) if include_sdm else None
    if sdm_path is not None:
        try:
            descriptions = parse_intel_sdm(sdm_path)
            _merge_descriptions(instructions, descriptions)
        except Exception:
            pass  # PDF parsing failure is non-fatal

    return Catalog(
        intrinsics=sorted(intrinsics, key=lambda item: item.name),
        instructions=sorted(instructions, key=lambda item: (item.mnemonic, item.form)),
        sources=[intel_source, uops_source],
        generated_at=now_iso(),
    )
