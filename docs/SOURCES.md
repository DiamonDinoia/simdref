# Upstream sources

simdref's catalog is built from eight upstream feeds. This document records
where each lives, how it's licensed, how often it's refreshed, and what's
currently missing — measured by `tools/audit_coverage.py` against a local
catalog build.

Run `python tools/audit_coverage.py report` to see the live coverage
summary. Snapshot at `docs/coverage/summary.json`.

---

## x86

### Intel Intrinsics Guide

- **URL:** <https://cdrdv2.intel.com/v1/dl/getContent/764289> (offline zip)
  and <https://www.intel.com/content/www/us/en/docs/intrinsics-guide/>
  (index HTML)
- **Format:** XML embedded in a JavaScript wrapper (`data.js`) inside a
  versioned zip.
- **License:** Intel permits redistribution of the guide with attribution;
  see the zip's `LICENSE.TXT`.
- **Refresh cadence:** Intel ships a new archive a few times a year (tied
  to new extensions or ISA additions). Pin the local `vendor/intel/` copy
  and bump on releases.
- **Known gaps:** None in the ~7k intrinsics the guide publishes.

### uops.info

- **URL:** <https://uops.info/instructions.xml>
- **Format:** XML, one `<instruction>` per entry with microarchitectural
  timings per CPU generation.
- **License:** Public research data, citation requested
  (<https://www.uops.info/about.html>).
- **Refresh cadence:** Multiple times per year.
- **Known gaps:** A handful of very new AVX-512 refinements may lag.

---

## Arm

### Arm ACLE intrinsics

- **URL:** <https://developer.arm.com/architectures/instruction-sets/intrinsics/data/intrinsics.json>
  plus ACLE spec on <https://arm-software.github.io/acle/>.
- **Format:** JSON payload; the Arm developer site hosts the canonical
  version consumed by compilers.
- **License:** Arm Developer site terms; ACLE spec is Apache-2.0.
- **Refresh cadence:** Aligned with ACLE releases (a few times per year).
- **Known gaps:** ~30% of upstream entries are missing from the catalog.
  The audit normalises upstream names by stripping bracketed
  alternatives (``[__arm_]vddupq[_n]_u8`` → ``vddupq_u8``), so this is a
  real ingestion shortfall, not a counting artefact — likely on the SVE
  or MVE side. Fixing it means extending
  ``parse_arm_intrinsics_payload`` in ``ingest_catalog.py``.

### Arm AARCHMRS (A64 instructions)

- **URL:** AARCHMRS tarball distributed on the Arm developer site.
- **Format:** Tar.gz containing large JSON machine-readable spec files.
- **License:** Arm EULA for the machine-readable spec.
- **Refresh cadence:** Follows Arm architecture revision (yearly).
- **Known gaps:** Offline snapshots use the fixture sample; live fetch
  (`SIMDREF_LIVE=1`) exercises the full spec.

---

## RISC-V

### RVV intrinsics

- **URL:** <https://github.com/riscv-non-isa/riscv-rvv-intrinsic-doc>
  — `auto-generated/intrinsics.json` and fallback locations.
- **Format:** JSON, ~75k entries per release.
- **License:** Apache-2.0.
- **Refresh cadence:** Driven by RVV spec revisions.
- **Known gaps:** None against the vendored snapshot (100% coverage).

### RISC-V unified DB (instructions)

- **URL:** <https://github.com/riscv-software-src/riscv-unified-db>
  — `generated/instructions.json` (and fallback paths).
- **Format:** JSON, ~700 instruction records plus HTML doc pages fetched
  from <https://docs.riscv.org/>.
- **License:** Apache-2.0.
- **Refresh cadence:** Continuous (active repo).
- **Known gaps:** None against the vendored snapshot (100% coverage).

---

## Microarchitectural perf data

simdref labels every perf row with a ``source_kind`` so users never
confuse modeled numbers for measurements.

### uops.info (x86, measured)

See above. All rows are ``source_kind="measured"``.

### LLVM scheduling models via llvm-exegesis → llvm-mc → llvm-mca (ARM + RISC-V, modeled)

- **Binaries:** ``llvm-exegesis``, ``llvm-mc``, and ``llvm-mca`` from
  LLVM 18+. All three live in the standard LLVM package.
- **Driver:** `src/simdref/perf_sources/llvm_scheduling.py` implements
  a three-stage structured pipeline per canonical core:
  1. ``llvm-exegesis --benchmark-phase=prepare-and-assemble-snippet``
     walks LLVM's own target-instruction table and emits a YAML
     document per schedulable opcode with an ``assembled_snippet`` hex
     stream (prologue + N × target + epilogue, or with a dependency-
     breaker interleaved when the opcode consumes its own output).
  2. The snippet's repeated instruction bytes are recovered by
     frequency-counting fixed-width chunks at natural ISA alignment —
     no regex, no asm-template synthesis.
  3. ``llvm-mc --disassemble`` turns the bytes into canonical
     assembly; ``llvm-mca --instruction-tables=full --json`` then
     measures ``Latency`` and ``RThroughput`` per line. The join key is
     the assembly mnemonic.
- **Runtime:** ~3 subprocess invocations per core (≈60 total for the
  13 AArch64 + 6 RISC-V cores) instead of ~55 000 one-snippet
  invocations; cold-cache end-to-end is well under a minute.
- **Cache:** intermediate artifacts under
  ``vendor/perf-cache/<triple>/<cpu>/{exegesis.yaml, disassembly.s,
  mca.json}``; rerunning on the same host short-circuits.
- **Coverage:** 13 AArch64 cores (Cortex-A72/76/78, Cortex-X1/X2,
  Neoverse-N1/N2/V1/V2, Apple M1/M2, A64FX, ThunderX2) and 6 RISC-V
  cores (SiFive U74/X280/P400/P600, XiangShan C908/C910, Spacemit X60).
- **License:** Apache-2.0 with LLVM exception.
- **Build-time requirement:** The ``--build`` pipeline aborts with an
  install hint when any of the three LLVM binaries is missing. End
  users who run ``simdref update`` without ``--build`` get the
  pre-built release catalog and do **not** need LLVM on PATH.

### RISC-V measured per-instruction perf (not available)

No public upstream publishes per-mnemonic measured RVV latency or
throughput. `camel-cdr/rvv-bench-results` — the only candidate that
was evaluated — publishes *kernel-level* benchmarks (memcpy, chacha20,
mandelbrot, etc.) with cycle counts per kernel variant, not tables per
RVV mnemonic. RISC-V per-core rows therefore come only from the
`llvm-mca` modeled pipeline.

---

## How refresh works

1. Edit `src/simdref/ingest_sources.py` candidate-URL lists when upstreams
   move.
2. `python -m simdref update` downloads the pre-built release catalog —
   no `llvm-mca` required.
3. `python -m simdref update --build` rebuilds from live sources and
   requires `llvm-exegesis`, `llvm-mc`, and `llvm-mca` on PATH. ARM and
   RISC-V per-core rows come from the three-stage scheduling pipeline
   (see **LLVM scheduling models** above); no regex-based asm synthesis
   is involved.
4. `python tools/audit_coverage.py fetch` re-runs extraction, compares
   against the freshly-built catalog, and rewrites
   `docs/coverage/summary.json`.
5. Commit the updated summary; `tests/test_coverage_parity.py` enforces
   the floors in `docs/coverage/thresholds.toml` on every CI run.
