# Upstream sources

simdref's catalog is built from nine upstream feeds. This document records
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

### LLVM scheduling models via llvm-mca (ARM + RISC-V, modeled)

- **Binary:** ``llvm-mca`` from LLVM 18+.
- **Driver:** `src/simdref/perf_sources/llvm_mca.py` runs
  ``llvm-mca --json --mtriple <t> --mcpu <c>`` per ``(triple, cpu)`` and
  parses per-instruction ``Latency`` + region ``IPC`` into rows with
  ``source_kind="modeled"``.
- **Coverage:** ~13 AArch64 cores (Cortex-A72/76/78, Neoverse-N1/N2/V1/V2,
  Apple M1/M2, A64FX, ThunderX2) and ~7 RISC-V cores (SiFive U74/X280/P400/P600,
  XiangShan/C908/C910, Spacemit-X60).
- **License:** Apache-2.0 with LLVM exception.
- **Build-time requirement:** The ``--build-local`` pipeline aborts with
  an install hint when ``llvm-mca`` is missing. End users who run
  ``simdref update`` without ``--build-local`` get the pre-built release
  catalog and do **not** need ``llvm-mca``.

### OSACA YAML (AArch64, measured)

- **Upstream:** <https://github.com/RRZE-HPC/OSACA>, pinned by commit SHA.
- **Driver:** `src/simdref/perf_sources/osaca.py` fetches the YAML in
  memory, parses it, and emits perf rows with ``source_kind="measured"``.
- **License boundary:** OSACA is AGPL-3.0. simdref **never vendors** the
  YAML or ships it in the wheel. Only our derived perf rows are
  serialized into the catalog — the AGPL scope stops at the build host.
- **Coverage:** Cortex-A72, Neoverse-N1, A64FX, ThunderX2.

### rvv-bench-results (RISC-V RVV, measured)

- **Upstream:** <https://github.com/camel-cdr/rvv-bench-results>, pinned
  by commit SHA.
- **Driver:** `src/simdref/perf_sources/rvv_bench.py` fetches the results
  JSON, joins on mnemonic × LMUL, and emits ``source_kind="measured"``
  rows citing <https://camel-cdr.github.io/rvv-bench-results/>.
- **Coverage:** C908, C910, Spacemit-X60 as of the pinned commit.
- **License:** MIT.

---

## Deferred sources

- **dougallj/applecpu** — Apple-Silicon measured perf. Deferred pending
  license clarification.
- **Arm SWOGs (Software Optimization Guides)** — redistribution
  restricted; surfaced as `pdf_refs` citation links only, never reparsed.
- **Full RISC-V scalar measured perf** — no public source exists;
  remains modeled-only via LLVM.

---

## How refresh works

1. Edit `src/simdref/ingest_sources.py` candidate-URL lists when upstreams
   move.
2. `python -m simdref update` downloads the pre-built release catalog —
   no `llvm-mca` required.
3. `python -m simdref update --build-local` rebuilds from live sources,
   requires `llvm-mca` on PATH, and runs OSACA/rvv-bench fetchers for
   measured ARM/RISC-V overlays.
4. `python tools/audit_coverage.py fetch` re-runs extraction, compares
   against the freshly-built catalog, and rewrites
   `docs/coverage/summary.json`.
5. Commit the updated summary; `tests/test_coverage_parity.py` enforces
   the floors in `docs/coverage/thresholds.toml` on every CI run.
