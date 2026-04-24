"""One-shot ``simdref profile run`` pipeline.

Stages are shelled out as subprocesses. Each stage writes into a
deterministic output directory; rerunning skips stages whose inputs match
their cached content hash.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(slots=True)
class StageResult:
    name: str
    cached: bool
    output: Path
    stderr: str = ""


def _hash_inputs(paths: Sequence[Path], extras: Sequence[str] = ()) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(str(p).encode())
        try:
            stat = p.stat()
            h.update(f"{stat.st_size}:{int(stat.st_mtime)}".encode())
        except OSError:
            pass
    for e in extras:
        h.update(b"|")
        h.update(e.encode())
    return h.hexdigest()[:16]


def _stage_cached(marker: Path, key: str) -> bool:
    if not marker.exists():
        return False
    try:
        return marker.read_text().strip() == key
    except OSError:
        return False


def _mark_stage(marker: Path, key: str) -> None:
    marker.write_text(key + "\n")


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise RuntimeError(f"required tool not found: {tool}")
    return path


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd)
    if res.returncode != 0:
        raise RuntimeError(
            f"command failed ({res.returncode}): {' '.join(cmd)}\nstderr:\n{res.stderr}"
        )
    return res


def run_pipeline(
    *,
    target: Path,
    args: list[str],
    outdir: Path,
    adapter: str = "perf",
    events: str = "cycles:pp,instructions",
    duration: float | None = None,
    arch: str | None = None,
    top_loops: int = 3,
    rank_event: str = "cycles",
) -> list[StageResult]:
    """Run the full compile→profile→annotate→merge pipeline.

    Returns a list of ``StageResult`` describing each stage (cached or run).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    results: list[StageResult] = []

    # --- Stage A: record ----------------------------------------------------
    perf_data = outdir / "perf.data"
    disasm = outdir / "disasm.s"
    annotated = outdir / "annotated.json"
    samples_json = outdir / "samples.json"
    loops_json = outdir / "loops.json"
    merged_sa = outdir / "hot.sa"
    merged_json = outdir / "merged.json"
    summary_md = outdir / "summary.md"

    if adapter == "perf":
        key = _hash_inputs([target], (events, *(args or []), str(duration or "")))
        marker = outdir / ".perf.stamp"
        if _stage_cached(marker, key) and perf_data.exists():
            results.append(StageResult("record", True, perf_data))
        else:
            perf = _require("perf")
            target_abs = str(target.resolve())
            cmd = [perf, "record", "-e", events, "-o", str(perf_data)]
            if duration is not None:
                cmd += ["--", "timeout", f"{duration}", target_abs, *args]
            else:
                cmd += ["--", target_abs, *args]
            res = _run(cmd)
            _mark_stage(marker, key)
            results.append(StageResult("record", False, perf_data, res.stderr))
    elif adapter == "mca":
        results.append(StageResult("record", True, Path("/dev/null")))
    else:
        raise ValueError(f"profile run: unsupported adapter '{adapter}' (use perf or mca)")

    # --- Stage B: objdump ---------------------------------------------------
    key = _hash_inputs([target])
    marker = outdir / ".disasm.stamp"
    if _stage_cached(marker, key) and disasm.exists():
        results.append(StageResult("disasm", True, disasm))
    else:
        objdump = _require("objdump")
        res = _run([objdump, "-dS", "--no-show-raw-insn", str(target)])
        disasm.write_text(res.stdout)
        _mark_stage(marker, key)
        results.append(StageResult("disasm", False, disasm))

    # --- Stage C: annotate (with position tracking) -------------------------
    key = _hash_inputs([disasm], (arch or "",))
    marker = outdir / ".annotate.stamp"
    if _stage_cached(marker, key) and annotated.exists():
        results.append(StageResult("annotate", True, annotated))
    else:
        _run_simdref(
            ["annotate", "--track-positions", "--format", "json", str(disasm), "-o", str(annotated)]
            + (["--arch", arch] if arch else [])
        )
        _mark_stage(marker, key)
        results.append(StageResult("annotate", False, annotated))

    # --- Stage D: ingest samples --------------------------------------------
    if adapter == "perf":
        key = _hash_inputs([perf_data, target])
        marker = outdir / ".ingest.stamp"
        if _stage_cached(marker, key) and samples_json.exists():
            results.append(StageResult("ingest", True, samples_json))
        else:
            _run_simdref(
                [
                    "profile",
                    "ingest",
                    "--adapter",
                    "perf",
                    "--binary",
                    str(target),
                    "--input",
                    str(perf_data),
                    "-o",
                    str(samples_json),
                ]
            )
            _mark_stage(marker, key)
            results.append(StageResult("ingest", False, samples_json))
    else:
        # mca: user-provided JSON somewhere; nothing to ingest automatically.
        # Leave an empty samples file so downstream merge still runs.
        if not samples_json.exists():
            samples_json.write_text('{"schema":"simdref.samples.v1","samples":[]}\n')
        results.append(StageResult("ingest", True, samples_json))

    # --- Stage E: hot loops -------------------------------------------------
    key = _hash_inputs([disasm, samples_json], (rank_event, str(top_loops)))
    marker = outdir / ".hotloops.stamp"
    if _stage_cached(marker, key) and loops_json.exists():
        results.append(StageResult("hotloops", True, loops_json))
    else:
        _run_simdref(
            [
                "profile",
                "hotloops",
                str(disasm),
                str(samples_json),
                "--event",
                rank_event,
                "--top",
                str(top_loops),
                "-o",
                str(loops_json),
            ]
        )
        _mark_stage(marker, key)
        results.append(StageResult("hotloops", False, loops_json))

    # --- Stage F: merge ------------------------------------------------------
    key = _hash_inputs([annotated, samples_json, loops_json])
    marker = outdir / ".merge.stamp"
    if _stage_cached(marker, key) and merged_sa.exists() and merged_json.exists():
        results.append(StageResult("merge", True, merged_sa))
    else:
        _run_simdref(
            [
                "profile",
                "merge",
                str(annotated),
                str(samples_json),
                "--restrict-to",
                str(loops_json),
                "--format",
                "sa",
                "-o",
                str(merged_sa),
            ]
        )
        _run_simdref(
            [
                "profile",
                "merge",
                str(annotated),
                str(samples_json),
                "--restrict-to",
                str(loops_json),
                "--format",
                "json",
                "-o",
                str(merged_json),
            ]
        )
        _mark_stage(marker, key)
        results.append(StageResult("merge", False, merged_sa))

    # --- Stage G: summary ----------------------------------------------------
    summary_md.write_text(_render_summary(loops_json, merged_json))
    results.append(StageResult("summary", False, summary_md))

    return results


def _run_simdref(args: list[str]) -> None:
    """Invoke the simdref CLI as a module; makes orchestration work in-tree."""
    import sys as _sys

    cmd = [_sys.executable, "-m", "simdref", *args]
    _run(cmd)


def _render_summary(loops_json: Path, merged_json: Path) -> str:
    loops = []
    if loops_json.exists():
        try:
            loops = json.loads(loops_json.read_text()).get("loops", [])
        except json.JSONDecodeError:
            pass
    merged = []
    if merged_json.exists():
        try:
            merged = json.loads(merged_json.read_text()).get("records", [])
        except json.JSONDecodeError:
            pass

    lines = ["# simdref profile summary", ""]
    if not loops:
        lines.append("_No hot loops detected._")
    else:
        lines.append("## Top loops")
        for l in loops[:3]:
            weight = l.get("total_weight", 0.0) * 100.0
            lines.append(
                f"- **loop #{l['loop_id']}** in `{l['symbol']}` "
                f"entry={l['entry_address']} weight={weight:.1f}%"
            )
        lines.append("")

    hot = [r for r in merged if (r.get("hotness") or {}).get("in_hot_loop") is True]
    if hot:
        hot.sort(
            key=lambda r: (
                -next(
                    iter(
                        v.get("weight", 0.0)
                        for k, v in (r.get("hotness") or {}).items()
                        if isinstance(v, dict)
                    ),
                    0.0,
                )
            )
        )
        lines.append("## Hottest instructions")
        for r in hot[:10]:
            first_event = next(
                ((k, v) for k, v in (r.get("hotness") or {}).items() if isinstance(v, dict)),
                None,
            )
            weight = first_event[1].get("weight", 0.0) * 100.0 if first_event else 0.0
            lines.append(
                f"- `{r.get('mnemonic', '')}` {r.get('annotation', '') or ''} — "
                f"{weight:.1f}% at {r.get('address', '?')}"
            )
    return "\n".join(lines) + "\n"
