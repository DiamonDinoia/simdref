"""Drive ``llvm-mca --json`` to produce modeled perf rows.

The ingester probes a ``(triple, cpu)`` pair for each mnemonic present in
the existing catalog and parses the emitted JSON into :class:`PerfRow`s
with ``source_kind="modeled"``. When ``llvm-mca`` is missing on the build
host the ingester raises :class:`LLVMMcaUnavailable` so ``simdref update
--build-local`` can surface a clear install hint instead of silently
falling back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable

from simdref.perf_sources.cores import CANONICAL_CORES, CoreSpec
from simdref.perf_sources.merge import PerfRow

LLVM_MCA_MIN_VERSION: int = 18  # JSON output stabilised in LLVM 18.

LLVM_MCA_CITATION = "https://llvm.org/docs/CommandGuide/llvm-mca.html"


class LLVMMcaUnavailable(RuntimeError):
    """Raised when ``llvm-mca`` is not on PATH or is too old.

    Carries an install hint string suitable for CLI display.
    """

    install_hint: str = (
        "Install llvm-mca to build the modeled perf catalog:\n"
        "  apt:    sudo apt install llvm\n"
        "  brew:   brew install llvm\n"
        "  conda:  conda install -c conda-forge llvm-tools\n"
        "Or download the pre-built release artifact: simdref update"
    )


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


def _run_llvm_mca(
    asm: str,
    triple: str,
    cpu: str,
    *,
    executable: str = "llvm-mca",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Run llvm-mca on a single asm snippet and return parsed JSON."""
    args = [
        executable,
        "--json",
        "--iterations=100",
        f"--mtriple={triple}",
        f"--mcpu={cpu}",
    ]
    try:
        proc = subprocess.run(
            args,
            input=asm,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise LLVMMcaUnavailable(f"llvm-mca invocation failed: {exc}") from exc
    return json.loads(proc.stdout)


def parse_llvm_mca_json(
    payload: dict[str, Any],
    *,
    core: CoreSpec,
    mnemonic: str,
    mca_version: str,
) -> PerfRow | None:
    """Extract one :class:`PerfRow` from an ``llvm-mca --json`` payload.

    The JSON format exposes a ``CodeRegions[0].Instructions`` list and a
    ``CodeRegions[0].SummaryView`` with throughput estimates. We pull the
    per-instruction ``Latency`` and the region-level ``IPC`` to derive CPI
    (``1/IPC`` rounded to 3dp).
    """
    try:
        regions = payload.get("CodeRegions") or []
        if not regions:
            return None
        region = regions[0]
        insts = region.get("Instructions") or []
        if not insts:
            return None
        first = insts[0]
        latency = first.get("Latency")
        summary = region.get("SummaryView") or {}
        ipc = summary.get("IPC")
        cpi = ""
        if isinstance(ipc, (int, float)) and ipc > 0:
            cpi = f"{1.0 / float(ipc):.3f}".rstrip("0").rstrip(".")
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
    except (KeyError, TypeError, ValueError):
        return None


def _asm_for(mnemonic: str, architecture: str) -> str:
    """Construct a minimal asm snippet llvm-mca can schedule.

    Hand-rolled operand templates cover the common SIMD cases; mnemonics
    we don't know are returned with no operands and llvm-mca is trusted to
    either schedule them or reject them (we skip on failure).
    """
    if architecture == "aarch64":
        return f"{mnemonic} v0.4s, v1.4s, v2.4s\n"
    if architecture == "riscv":
        return f"{mnemonic} v0, v1, v2\n"
    return f"{mnemonic}\n"


def ingest_llvm_mca(
    mnemonics_by_arch: dict[str, Iterable[str]],
    *,
    cores: Iterable[CoreSpec] | None = None,
    executable: str = "llvm-mca",
) -> tuple[list[PerfRow], str]:
    """Probe llvm-mca across cores × mnemonics, returning rows and version.

    *mnemonics_by_arch* maps architecture family (``"aarch64"``, ``"riscv"``)
    to the mnemonic set to probe for that family.

    Raises :class:`LLVMMcaUnavailable` when llvm-mca is absent or too old;
    individual mnemonic/core failures are swallowed so one bad instruction
    does not abort the whole ingestion.
    """
    version = detect_llvm_mca_version(executable)
    rows: list[PerfRow] = []
    target_cores = list(cores) if cores is not None else list(CANONICAL_CORES)
    for core in target_cores:
        if core.architecture == "x86":
            continue
        mnemonics = list(mnemonics_by_arch.get(core.architecture, []))
        for mnemonic in mnemonics:
            asm = _asm_for(mnemonic, core.architecture)
            try:
                payload = _run_llvm_mca(asm, core.llvm_triple, core.llvm_cpu, executable=executable)
            except LLVMMcaUnavailable:
                raise
            except Exception:
                continue
            row = parse_llvm_mca_json(
                payload, core=core, mnemonic=mnemonic, mca_version=version.raw
            )
            if row is not None:
                rows.append(row)
    return rows, version.raw
