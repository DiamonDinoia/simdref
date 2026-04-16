"""Source acquisition for intrinsics and instruction catalogs."""

from __future__ import annotations

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

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_INTEL_ARCHIVES = [
    _REPO_ROOT / "vendor" / "intel" / "Intel-Intrinsics-Guide-Offline-3.6.9.zip",
]
LOCAL_UOPS_XMLS = [
    _REPO_ROOT / "vendor" / "uops" / "instructions.xml",
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
