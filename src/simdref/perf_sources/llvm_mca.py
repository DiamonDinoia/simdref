"""Drive the llvm-exegesis → llvm-mc → llvm-mca pipeline per core.

The ingester no longer synthesises per-mnemonic asm snippets. It
enumerates every LLVM-schedulable opcode per target ``(triple, cpu)``
via ``llvm-exegesis``, disassembles the captured bytes, and feeds the
result through ``llvm-mca --instruction-tables=full --json``. Three
subprocess calls per core replace the ~55,000 calls of the former
regex-based synthesiser.

Failure modes:

- ``llvm-mca`` / ``llvm-exegesis`` / ``llvm-mc`` absent or too old:
  :class:`LLVMMcaUnavailable`, which the CLI surfaces with an install
  hint.
- any subprocess emits non-zero status or unparseable output:
  :class:`LLVMMcaError` with the offending core's triple + cpu.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable

from simdref.models import InstructionRecord
from simdref.perf_sources.cores import CANONICAL_CORES, CoreSpec
from simdref.perf_sources.merge import PerfRow

LLVM_MCA_MIN_VERSION: int = 18  # JSON output stabilised in LLVM 18.
LLVM_MCA_CITATION = "https://llvm.org/docs/CommandGuide/llvm-mca.html"


class LLVMMcaUnavailable(RuntimeError):
    """Raised when a required LLVM binary is missing or too old."""

    install_hint: str = (
        "Install the LLVM toolchain (llvm-mca, llvm-exegesis, llvm-mc) to "
        "build the modeled perf catalog:\n"
        "  apt:    sudo apt install llvm\n"
        "  brew:   brew install llvm\n"
        "  conda:  conda install -c conda-forge llvm-tools\n"
        "Or download the pre-built release artifact: simdref update"
    )


class LLVMMcaError(RuntimeError):
    """Raised when one of the pipeline stages emits unusable output."""


@dataclass(frozen=True)
class LLVMMcaVersion:
    major: int
    raw: str


def detect_llvm_mca_version(executable: str = "llvm-mca") -> LLVMMcaVersion:
    """Return the LLVM major version, or raise :class:`LLVMMcaUnavailable`."""
    if shutil.which(executable) is None:
        raise LLVMMcaUnavailable(f"{executable!r} not found on PATH")
    try:
        proc = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise LLVMMcaUnavailable(f"failed to run {executable} --version: {exc}") from exc
    for line in (proc.stdout + proc.stderr).splitlines():
        token = line.lower()
        if "llvm version" in token:
            tail = token.split("llvm version", 1)[1].strip().split()[0]
            head = tail.split(".", 1)[0]
            if head.isdigit():
                major = int(head)
                if major < LLVM_MCA_MIN_VERSION:
                    raise LLVMMcaUnavailable(
                        f"llvm-mca {major} is older than required {LLVM_MCA_MIN_VERSION}"
                    )
                return LLVMMcaVersion(major=major, raw=tail)
    raise LLVMMcaUnavailable(f"could not parse version from {executable} --version")


def parse_llvm_mca_json(
    payload: dict[str, Any],
    *,
    core: CoreSpec,
    mnemonic: str,
    mca_version: str,
) -> PerfRow | None:
    """Extract one :class:`PerfRow` from a single-region ``llvm-mca --json`` payload.

    Kept as a small utility for callers that want to run llvm-mca on a
    standalone snippet (outside the scheduling pipeline). It tolerates
    both the modern ``InstructionInfoView`` schema (LLVM 18+) and the
    legacy flat ``Instructions`` + ``SummaryView.IPC`` schema used by
    older fixtures.
    """
    regions = payload.get("CodeRegions") or []
    if not regions:
        return None
    region = regions[0]
    info_view = region.get("InstructionInfoView") or {}
    info_list = info_view.get("InstructionList") or []
    latency: Any = None
    rthroughput: Any = None
    if info_list and isinstance(info_list[0], dict):
        latency = info_list[0].get("Latency")
        rthroughput = info_list[0].get("RThroughput")
    if latency is None and rthroughput is None:
        insts = region.get("Instructions") or []
        if insts and isinstance(insts[0], dict):
            latency = insts[0].get("Latency")
        summary = region.get("SummaryView") or {}
        ipc = summary.get("IPC")
        if isinstance(ipc, (int, float)) and ipc > 0:
            rthroughput = 1.0 / float(ipc)
    cpi = ""
    if isinstance(rthroughput, (int, float)):
        cpi = f"{float(rthroughput):.3f}".rstrip("0").rstrip(".")
    return PerfRow(
        mnemonic=mnemonic,
        core=core.canonical_id,
        source="llvm-mca",
        source_kind="modeled",
        source_version=mca_version,
        architecture="arm" if core.architecture == "aarch64" else core.architecture,
        latency=str(latency) if latency is not None else "",
        cpi=cpi,
        applies_to="mnemonic",
        citation_url=LLVM_MCA_CITATION,
    )


def ingest_llvm_mca(
    records: Iterable[InstructionRecord],
    *,
    cores: Iterable[CoreSpec] | None = None,
    executable: str = "llvm-mca",
    cache_root: Any = None,
) -> tuple[list[PerfRow], str]:
    """Enumerate LLVM-schedulable opcodes across cores, returning rows + version.

    Each aarch64 / riscv core triggers one llvm-exegesis +
    llvm-mc + llvm-mca pipeline, cached under ``vendor/perf-cache/``.
    x86 cores are skipped (scheduling models come from uops.info).

    *records* is accepted for API symmetry with the previous implementation
    but is no longer consumed — the pipeline reads LLVM's own InstrInfo
    tables rather than the catalog.

    Raises :class:`LLVMMcaUnavailable` when a required binary is absent
    or too old. Raises :class:`LLVMMcaError` on any pipeline failure —
    no silent fallback.
    """
    # Lazy import to break the circular dependency (llvm_scheduling
    # imports the error classes + citation URL from this module).
    from simdref.perf_sources import llvm_scheduling  # noqa: PLC0415

    version = detect_llvm_mca_version(executable)
    _ = list(records)  # drain iterator; kept for signature compatibility
    target_cores = list(cores) if cores is not None else list(CANONICAL_CORES)
    rows: list[PerfRow] = []
    for core in target_cores:
        if core.architecture == "x86":
            continue
        rows.extend(
            llvm_scheduling.collect_core_schedule(
                core,
                mca_version=version.raw,
                cache_root=cache_root,
            )
        )
    return rows, version.raw
