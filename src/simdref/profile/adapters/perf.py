"""Linux ``perf`` adapter.

Accepts a ``perf.data`` file (we shell out to ``perf script``) or
pre-captured ``perf script`` text output produced with the exact ``-F``
list we expect::

    perf script -F period,event,ip,sym,symoff,dso

Line shape::

    <period> <event>: <ip> <sym>+0x<symoff> (<dso>)

For PIE binaries, the runtime ``ip`` differs from the file-relative VA
that ``objdump -d`` prints (ASLR randomises the load base). To produce
addresses that join cleanly with an objdump listing we resolve each
sample's ``(sym, symoff)`` against the binary's own symbol table (via
``nm``) and emit ``sym_va + symoff``. When ``--binary`` is not provided
we fall back to raw runtime IPs (useful for pre-normalised text input).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from simdref.profile.model import SampleRow
from simdref.profile.registry import register_profiler


def _canon_event(name: str) -> str:
    """Normalise hybrid-CPU PMU names (``cpu_core/cycles/u`` → ``cycles``).

    We keep the core event (cycles, instructions, branches, cache-misses,
    ...) so that downstream ranking can use the canonical name across
    hybrid and non-hybrid hardware.
    """
    n = name
    # Strip leading "cpu_core/" or "cpu_atom/" prefix (Intel hybrid PMU).
    m = re.match(r"^cpu_[^/]+/(.*)$", n)
    if m:
        n = m.group(1)
    # Strip trailing "/u", "/k", "/up", ... (monitoring modifier suffix).
    m = re.match(r"^(.*)/[a-zA-Z]{1,3}$", n)
    if m:
        n = m.group(1)
    # Trailing ":u", ":pp", ":upp" modifiers.
    if ":" in n:
        head, _, tail = n.partition(":")
        if tail in {"u", "k", "up", "pp", "upp", "P"}:
            n = head
    return n


# Line: "<period> <event>: <ip> <sym>+0x<off> (<dso>)"
_SAMPLE_RE = re.compile(
    r"^\s*(?P<period>\d+)\s+"
    r"(?P<event>\S+?):\s+"
    r"(?P<ip>[0-9a-fA-F]+)\s+"
    r"(?P<sym>\S+?)\+0x(?P<symoff>[0-9a-fA-F]+)"
    r"(?:\s+\((?P<dso>[^)]+)\))?\s*$"
)


def _looks_like_perf_data(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            magic = f.read(8)
    except OSError:
        return False
    return magic.startswith(b"PERFILE2") or magic.startswith(b"PERFFILE")


def _run_perf_script(perf_data: Path) -> str:
    perf = shutil.which("perf")
    if perf is None:
        raise RuntimeError("perf binary not found in PATH; cannot decode perf.data")
    cmd = [
        perf,
        "script",
        "-i",
        str(perf_data),
        "-F",
        "period,event,ip,sym,symoff,dso",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"`perf script` failed: {res.stderr.strip()}")
    return res.stdout


def _parse_script_lines(
    text: str,
) -> Iterator[tuple[int, str, int, str, int, str]]:
    """Yield ``(ip, event, period, sym, symoff, dso)``."""
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        m = _SAMPLE_RE.match(raw)
        if not m:
            continue
        try:
            period = int(m.group("period"))
            ip = int(m.group("ip"), 16)
            symoff = int(m.group("symoff"), 16)
        except ValueError:
            continue
        yield (
            ip,
            _canon_event(m.group("event")),
            period,
            m.group("sym"),
            symoff,
            m.group("dso") or "",
        )


def _load_symbol_table(binary: Path) -> dict[str, int]:
    """Map symbol name → file-relative VA via ``nm``."""
    nm = shutil.which("nm")
    if nm is None:
        return {}
    res = subprocess.run(
        [nm, "--defined-only", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return {}
    out: dict[str, int] = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            va = int(parts[0], 16)
        except ValueError:
            continue
        sym = parts[-1]
        out[sym] = va
    return out


class _PerfAdapter:
    id = "perf"
    description = "Linux perf (perf.data or `perf script` text)"

    def can_handle(self, path: Path) -> bool:
        if not path.exists() or path.is_dir():
            return False
        if _looks_like_perf_data(path):
            return True
        try:
            head = path.read_text(errors="replace")[:4096]
        except OSError:
            return False
        return "cycles" in head or "instructions" in head or "perf script" in head

    def ingest(self, path: Path, *, binary: Path | None) -> Iterable[SampleRow]:
        if _looks_like_perf_data(path):
            text = _run_perf_script(path)
        else:
            text = path.read_text(errors="replace")

        sym_va: dict[str, int] = _load_symbol_table(binary) if binary else {}
        binary_basename = os.path.basename(str(binary)) if binary else None
        binary_abs = str(binary.resolve()) if binary else None

        # Aggregate per (file-offset address, event).
        agg: dict[tuple[int, str], tuple[int, int, str]] = defaultdict(lambda: (0, 0, ""))
        for ip, event, period, sym, symoff, dso in _parse_script_lines(text):
            if binary_basename is not None:
                # Keep only samples attributable to our binary.
                if not dso:
                    continue
                if os.path.basename(dso) != binary_basename and dso != binary_abs:
                    continue
                # Prefer sym_va + symoff; fall back to raw ip if we can't resolve.
                base = sym_va.get(sym)
                addr = (base + symoff) if base is not None else ip
            else:
                addr = ip

            samples, weight_accum, old_sym = agg[(addr, event)]
            agg[(addr, event)] = (samples + 1, weight_accum + period, sym or old_sym)

        totals: dict[str, int] = defaultdict(int)
        for (_, event), (_, weight_accum, _) in agg.items():
            totals[event] += weight_accum

        src_map: dict[int, tuple[str | None, int | None]] = {}
        if binary:
            src_map = _resolve_addr2line(binary, sorted({a for (a, _) in agg}))

        for (addr, event), (samples, weight_accum, sym) in sorted(agg.items()):
            total = totals[event] or 1
            sf, sl = src_map.get(addr, (None, None))
            yield SampleRow(
                address=addr,
                event=event,
                samples=samples,
                weight=weight_accum / total,
                symbol=sym,
                source_file=sf,
                source_line=sl,
            )


def _resolve_addr2line(
    binary: Path, addresses: list[int]
) -> dict[int, tuple[str | None, int | None]]:
    """Best-effort addr2line. Returns empty map if tool missing or binary lacks -g."""
    a2l = shutil.which("addr2line")
    if a2l is None or not addresses:
        return {}
    cmd = [a2l, "-e", str(binary), "-C"]
    inp = "\n".join(f"0x{a:x}" for a in addresses) + "\n"
    try:
        res = subprocess.run(cmd, input=inp, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    if res.returncode != 0:
        return {}
    out: dict[int, tuple[str | None, int | None]] = {}
    lines = res.stdout.splitlines()
    for addr, line in zip(addresses, lines):
        if ":" not in line:
            out[addr] = (None, None)
            continue
        file_part, _, line_part = line.rpartition(":")
        fname = file_part if file_part and file_part != "??" else None
        try:
            ln = int(line_part.split()[0].split(" ")[0])
        except (ValueError, IndexError):
            ln = None
        out[addr] = (fname, ln)
    return out


register_profiler(_PerfAdapter())
