"""``llvm-mca -json`` adapter.

``llvm-mca`` is available everywhere LLVM is, so this adapter is the
static-only fallback when no hardware sampler is available (CI containers,
restricted envs). The "samples" it produces are modeled cycles per
instruction, not observations, and are tagged ``source_kind="modeled"``.

We don't have real VAs from llvm-mca output (it is source-level). The
adapter emits one sample per instruction with a synthetic address derived
from the instruction index — the merge layer falls back to
``(source_file, source_line)`` joins when addresses don't line up, so this
still works in practice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from simdref.profile.model import SampleRow
from simdref.profile.registry import register_profiler


class _McaAdapter:
    id = "mca"
    description = "llvm-mca -json (modeled, static fallback)"

    def can_handle(self, path: Path) -> bool:
        if not path.exists() or path.is_dir():
            return False
        try:
            with path.open("rb") as f:
                head = f.read(2048)
        except OSError:
            return False
        # A minimal sniff: JSON object containing MCA keys.
        head_s = head.decode("utf-8", errors="replace")
        return head_s.lstrip().startswith("{") and ("CodeRegions" in head_s or "Instructions" in head_s)

    def ingest(self, path: Path, *, binary: Path | None) -> Iterable[SampleRow]:
        data = json.loads(path.read_text())
        regions = data.get("CodeRegions") or data.get("Regions") or []
        total = 0.0
        emitted: list[tuple[int, float, str]] = []

        idx = 0
        for region in regions:
            summary = region.get("SummaryView") or {}
            instructions = region.get("Instructions") or region.get("InstructionInfoView", {}).get("InstructionList") or []
            # Total cycles for the region, fall back to iterations * count.
            region_cycles = float(summary.get("TotalCycles", 0) or 0)
            n_insts = len(instructions) or 1
            per_inst = region_cycles / n_insts if region_cycles else 1.0

            for inst in instructions:
                mnemonic = (
                    inst.get("Instruction")
                    or inst.get("Mnemonic")
                    or inst.get("OpcodeName")
                    or ""
                )
                # Synthesize address: 4 bytes per instruction from idx=0.
                synth_addr = 0x1000 + idx * 4
                emitted.append((synth_addr, per_inst, str(mnemonic)))
                total += per_inst
                idx += 1

        total = total or 1.0
        for addr, cycles, mnemonic in emitted:
            yield SampleRow(
                address=addr,
                event="mca:cycles",
                samples=int(round(cycles * 100)),
                weight=cycles / total,
                symbol=mnemonic,
                source_kind="modeled",
            )


register_profiler(_McaAdapter())
