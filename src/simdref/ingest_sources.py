"""Source acquisition for intrinsics and instruction catalogs."""

from __future__ import annotations

import json
import io
import re
import zipfile
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import httpx

from simdref.models import SourceVersion

UOPS_XML_URL = "https://uops.info/instructions.xml"
INTEL_OFFLINE_ZIP_URL = "https://cdrdv2.intel.com/v1/dl/getContent/764289?fileName=Intel-Intrinsics-Guide-Offline-3.6.4.zip"
INTEL_INDEX_URL = "https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html"
INTEL_CANDIDATE_DATA_URLS = [
    "https://www.intel.com/content/dam/develop/public/us/en/include/intrinsics-guide/files/data.js",
    "https://www.intel.com/content/dam/develop/public/us/en/include/intrinsics-guide/files/intrinsics.json",
]
ARM_ACLE_REPO_URL = "https://github.com/ARM-software/acle"
ARM_ACLE_DOC_URL = "https://arm-software.github.io/acle/main/"
ARM_NEON_DOC_URL = "https://arm-software.github.io/acle/neon_intrinsics/advsimd.html"
ARM_A64_DOC_URL = "https://developer.arm.com/documentation/ddi0602/latest/Base-Instructions"
ARM_ACLE_ARCHIVE_URL = "https://codeload.github.com/ARM-software/acle/zip/refs/heads/main"
ARM_INTRINSICS_DATA_BASE_URL = "https://developer.arm.com/architectures/instruction-sets/intrinsics/data/"
ARM_INTRINSICS_JSON_URL = ARM_INTRINSICS_DATA_BASE_URL + "intrinsics.json"
ARM_INTRINSICS_OPERATIONS_JSON_URL = ARM_INTRINSICS_DATA_BASE_URL + "operations.json"
ARM_INTRINSICS_EXAMPLES_JSON_URL = ARM_INTRINSICS_DATA_BASE_URL + "examples.json"
ARM_ACLE_NEON_DB_PATH = "tools/intrinsic_db/advsimd.csv"
ARM_ACLE_NEON_CLASSIFICATION_PATH = "tools/intrinsic_db/advsimd_classification.csv"
ARM_ACLE_MAIN_MD_PATH = "main/acle.md"
ARM_ACLE_NEON_MD_PATH = "neon_intrinsics/advsimd.md"

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_INTEL_ARCHIVES = [
    _REPO_ROOT / "vendor" / "intel" / "Intel-Intrinsics-Guide-Offline-3.6.9.zip",
]
LOCAL_UOPS_XMLS = [
    _REPO_ROOT / "vendor" / "uops" / "instructions.xml",
]
LOCAL_ARM_ACLE_JSONS = [
    _REPO_ROOT / "vendor" / "arm" / "acle_intrinsics.json",
]
LOCAL_ARM_INTRINSICS_JSONS = [
    _REPO_ROOT / "vendor" / "arm" / "intrinsics.json",
]
LOCAL_ARM_OPERATIONS_JSONS = [
    _REPO_ROOT / "vendor" / "arm" / "operations.json",
]
LOCAL_ARM_EXAMPLES_JSONS = [
    _REPO_ROOT / "vendor" / "arm" / "examples.json",
]
LOCAL_ARM_ACLE_ARCHIVES = [
    _REPO_ROOT / "vendor" / "arm" / "acle-main.zip",
]
LOCAL_ARM_A64_JSONS = [
    _REPO_ROOT / "vendor" / "arm" / "a64_instructions.json",
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


def fetch_uops_xml(offline: bool = False) -> tuple[str | Path, SourceVersion]:
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
            return xml_path, SourceVersion(
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

    for url in INTEL_CANDIDATE_DATA_URLS:
        try:
            text = _fetch_text(url)
            return text, SourceVersion(
                source="intel-intrinsics-guide",
                version="live",
                fetched_at=now_iso(),
                url=url,
            )
        except Exception:
            pass
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
            except Exception:
                pass
    except Exception:
        pass

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
    except Exception:
        pass

    return fetch_intel_data(offline=True)


def _read_local_text(paths: list[Path], source: str, version_prefix: str) -> tuple[str, SourceVersion] | None:
    for text_path in paths:
        if not text_path.exists():
            continue
        return text_path.read_text(), SourceVersion(
            source=source,
            version=f"{version_prefix}:{text_path.name}",
            fetched_at=now_iso(),
            url=str(text_path),
        )
    return None


def _arm_acle_bundle_payload(
    intrinsics_csv: str,
    classification_csv: str,
    acle_markdown: str = "",
    neon_markdown: str = "",
) -> str:
    return json.dumps(
        {
            "format": "acle-neon-csv-v1",
            "intrinsics_csv": intrinsics_csv,
            "classification_csv": classification_csv,
            "acle_markdown": acle_markdown,
            "neon_markdown": neon_markdown,
        }
    )


def _arm_intrinsics_bundle_payload(intrinsics_json: str, operations_json: str, examples_json: str = "[]") -> str:
    return json.dumps(
        {
            "format": "arm-intrinsics-json-v1",
            "intrinsics_json": intrinsics_json,
            "operations_json": operations_json,
            "examples_json": examples_json,
        }
    )


def _extract_zip_text(zf: zipfile.ZipFile, suffix: str) -> str:
    for name in zf.namelist():
        if name.endswith(suffix):
            return zf.read(name).decode("utf-8", "replace")
    raise KeyError(suffix)


def _read_local_arm_acle_archive() -> tuple[str, SourceVersion] | None:
    for archive_path in LOCAL_ARM_ACLE_ARCHIVES:
        if not archive_path.exists():
            continue
        with zipfile.ZipFile(archive_path) as zf:
            return _arm_acle_bundle_payload(
                _extract_zip_text(zf, ARM_ACLE_NEON_DB_PATH),
                _extract_zip_text(zf, ARM_ACLE_NEON_CLASSIFICATION_PATH),
                _extract_zip_text(zf, ARM_ACLE_MAIN_MD_PATH),
                _extract_zip_text(zf, ARM_ACLE_NEON_MD_PATH),
            ), SourceVersion(
                source="arm-acle",
                version=f"archive:{archive_path.name}",
                fetched_at=now_iso(),
                url=str(archive_path),
            )
    return None


def _read_local_arm_intrinsics_bundle() -> tuple[str, SourceVersion] | None:
    intrinsics = _read_local_text(LOCAL_ARM_INTRINSICS_JSONS, "arm-intrinsics-site", "local-json")
    operations = _read_local_text(LOCAL_ARM_OPERATIONS_JSONS, "arm-intrinsics-site", "local-json")
    if intrinsics is None or operations is None:
        return None
    examples = _read_local_text(LOCAL_ARM_EXAMPLES_JSONS, "arm-intrinsics-site", "local-json")
    payload = _arm_intrinsics_bundle_payload(
        intrinsics[0],
        operations[0],
        examples[0] if examples is not None else "[]",
    )
    return payload, SourceVersion(
        source="arm-intrinsics-site",
        version="local-json-bundle",
        fetched_at=now_iso(),
        url=str(LOCAL_ARM_INTRINSICS_JSONS[0].parent),
    )


def refresh_local_arm_intrinsics_bundle() -> list[Path]:
    target_dir = LOCAL_ARM_INTRINSICS_JSONS[0].parent
    target_dir.mkdir(parents=True, exist_ok=True)
    downloads = [
        (ARM_INTRINSICS_JSON_URL, LOCAL_ARM_INTRINSICS_JSONS[0]),
        (ARM_INTRINSICS_OPERATIONS_JSON_URL, LOCAL_ARM_OPERATIONS_JSONS[0]),
        (ARM_INTRINSICS_EXAMPLES_JSON_URL, LOCAL_ARM_EXAMPLES_JSONS[0]),
    ]
    written: list[Path] = []
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        for url, path in downloads:
            response = client.get(url)
            response.raise_for_status()
            path.write_text(response.text)
            written.append(path)
    return written


def fetch_arm_acle_data(offline: bool = False) -> tuple[str, SourceVersion]:
    if offline:
        return _fixture_text("arm_acle_intrinsics_sample.json"), SourceVersion(
            source="arm-acle",
            version="fixture",
            fetched_at=now_iso(),
            url="fixture:arm_acle_intrinsics_sample.json",
            used_fixture=True,
        )
    local_intrinsics_bundle = _read_local_arm_intrinsics_bundle()
    if local_intrinsics_bundle is not None:
        return local_intrinsics_bundle
    local_archive = _read_local_arm_acle_archive()
    if local_archive is not None:
        return local_archive
    local = _read_local_text(LOCAL_ARM_ACLE_JSONS, "arm-acle", "local-json")
    if local is not None:
        return local
    try:
        payload = _arm_intrinsics_bundle_payload(
            _fetch_text(ARM_INTRINSICS_JSON_URL),
            _fetch_text(ARM_INTRINSICS_OPERATIONS_JSON_URL),
            _fetch_text(ARM_INTRINSICS_EXAMPLES_JSON_URL),
        )
        return payload, SourceVersion(
            source="arm-intrinsics-site",
            version="live-json",
            fetched_at=now_iso(),
            url=ARM_INTRINSICS_JSON_URL,
        )
    except Exception:
        pass
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            resp = client.get(ARM_ACLE_ARCHIVE_URL)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                payload = _arm_acle_bundle_payload(
                    _extract_zip_text(zf, ARM_ACLE_NEON_DB_PATH),
                    _extract_zip_text(zf, ARM_ACLE_NEON_CLASSIFICATION_PATH),
                    _extract_zip_text(zf, ARM_ACLE_MAIN_MD_PATH),
                    _extract_zip_text(zf, ARM_ACLE_NEON_MD_PATH),
                )
            return payload, SourceVersion(
                source="arm-acle",
                version="archive-download",
                fetched_at=now_iso(),
                url=ARM_ACLE_ARCHIVE_URL,
            )
    except Exception:
        pass
    return fetch_arm_acle_data(offline=True)


def fetch_arm_a64_data(offline: bool = False) -> tuple[str, SourceVersion]:
    if offline:
        return _fixture_text("arm_a64_instructions_sample.json"), SourceVersion(
            source="arm-a64",
            version="fixture",
            fetched_at=now_iso(),
            url="fixture:arm_a64_instructions_sample.json",
            used_fixture=True,
        )
    local = _read_local_text(LOCAL_ARM_A64_JSONS, "arm-a64", "local-json")
    if local is not None:
        return local
    return fetch_arm_a64_data(offline=True)
