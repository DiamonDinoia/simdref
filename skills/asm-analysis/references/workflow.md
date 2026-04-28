# asm-analysis shared workflow

Pipeline: **input → compile line → `.s` → `simdref annotate` → `simdref llm batch` → proposal**. Every stage below is mandatory unless it says otherwise.

______________________________________________________________________

## 0. Preflight (runs once before any pipeline stage)

Probe the installed version:

```bash
simdref --version   # or: simdref -V
```

### 0a. Not installed

Ask the user which of these to use, then proceed. Prefer **installing
from `main`** — the released PyPI build lags behind and omits the
`simdref profile` subcommand the profile-driven workflow in §2b depends
on.

1. **From GitHub `main` via pipx (recommended):**
   ```bash
   pipx install "git+https://github.com/DiamonDinoia/simdref.git@main"
   ```
1. **From GitHub `main` into a project-local venv:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install "git+https://github.com/DiamonDinoia/simdref.git@main"
   ```
   Subsequent `simdref …` calls require the venv to be active (or use the absolute path `.venv/bin/simdref`).
1. **From a local source checkout (if the user has the repo cloned):**
   ```bash
   python -m simdref <args...>           # invoke from the repo root
   # or editable inside a venv:
   pip install -e /path/to/simdref
   ```
1. **Released PyPI build (simplest, but missing `simdref profile`):**
   ```bash
   pipx install simdref
   ```
1. **Transient per-invocation via uvx (no install state):**
   ```bash
   uvx --from "git+https://github.com/DiamonDinoia/simdref.git@main" simdref <args...>
   ```
   In this mode, prepend the `uvx --from ...` prefix to every `simdref …` invocation in the rest of this skill.

Pre-release PyPI builds live on TestPyPI:

```bash
pipx install simdref --pip-args "--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/"
```

Never install without asking.

**Feature check:** if the user's existing install doesn't respond to
`simdref profile --help` but Stage 2b is needed, offer to upgrade to
`main` via the first option above before falling back to the hand-picked
region flow in §3.

### 0b. Already installed — freshness probe (run at most once per session)

Before the first real pipeline run in a conversation, check both the
package *and* this skill for upstream changes. The probe below is
self-contained: it compares installed state against
`github.com/DiamonDinoia/simdref`, caches the result under
`~/.cache/simdref-asm-skill/` with a 6-hour TTL, and surfaces exactly
one notice when something is newer.

**Run this block once per session (skip if the cache file is fresh):**

```bash
mkdir -p ~/.cache/simdref-asm-skill
if [ -f ~/.cache/simdref-asm-skill/disabled ]; then
  echo "freshness probe disabled by user"
  exit 0
fi
CACHE=~/.cache/simdref-asm-skill/last-check
if [ -f "$CACHE" ] && [ $(( $(date +%s) - $(stat -c %Y "$CACHE" 2>/dev/null || stat -f %m "$CACHE") )) -lt 21600 ]; then
  echo "skipping freshness check (cached <6h ago)"
else
  # 1. Installed package version
  installed=$(simdref --version 2>/dev/null | awk '{print $NF}')

  # 2. Latest PyPI release
  pypi=$(curl -fsSL https://pypi.org/pypi/simdref/json | python -c "import json,sys; print(json.load(sys.stdin)['info']['version'])" 2>/dev/null || echo "?")

  # 3. Latest main-branch commit touching any simdref source.
  # /commits/main returns a single commit object, so index the dict directly.
  main_sha=$(curl -fsSL "https://api.github.com/repos/DiamonDinoia/simdref/commits/main" | python -c "import json,sys; print(json.load(sys.stdin)['sha'][:7])" 2>/dev/null || echo "?")

  # 4. Latest main-branch commit touching THIS skill file
  skill_sha=$(curl -fsSL "https://api.github.com/repos/DiamonDinoia/simdref/commits?path=skills/asm-analysis/references/workflow.md&per_page=1" | python -c "import json,sys; print(json.load(sys.stdin)[0]['sha'][:7])" 2>/dev/null || echo "?")

  # 5. Number of unreleased commits on main past the latest tag
  unreleased=$(curl -fsSL "https://api.github.com/repos/DiamonDinoia/simdref/compare/v${pypi}...main" | python -c "import json,sys; print(json.load(sys.stdin).get('ahead_by', 0))" 2>/dev/null || echo "?")

  printf 'installed=%s  pypi=%s  main-HEAD=%s  workflow-HEAD=%s  unreleased=%s\n' \
    "$installed" "$pypi" "$main_sha" "$skill_sha" "$unreleased"
  date +%s > "$CACHE"
  echo "$installed|$pypi|$main_sha|$skill_sha|$unreleased" > "$CACHE.state"
fi
```

Decision table for what to surface to the user (pick the **first** row
that matches; do not nag a second time in the same session):

| Condition                                                                                        | Ask the user                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `installed < pypi`                                                                               | Minor-release available — offer `pipx upgrade simdref`.                                                                                                               |
| `unreleased >= 1` and skill does not need `simdref profile`                                      | Pre-release with N commits past `v$pypi`. Offer `pipx install --force git+https://github.com/DiamonDinoia/simdref.git@main`. Mention this is optional.                |
| `simdref profile --help` fails and Stage 2b is likely needed                                     | **Required** upgrade to main to unlock the profile pipeline — install from `main`.                                                                                    |
| `skill_sha` differs from the last one stored in `~/.cache/simdref-asm-skill/last-seen-skill-sha` | Skill workflow has been updated upstream. Offer the product-specific update path from the skill entrypoint, or `(cd ~/src/simdref && git pull)` for symlink installs. |
| None of the above                                                                                | Silent — record the state and continue.                                                                                                                               |

After surfacing (and the user accepts or declines), record the seen
skill SHA so we don't re-prompt on the same upstream update:

```bash
# Only after the user has seen the notice (accept or decline):
echo "$skill_sha" > ~/.cache/simdref-asm-skill/last-seen-skill-sha
```

**Offline / rate-limited** (any `curl` above returns `?`): skip the
check silently. Do not block the pipeline on network failures.

**Privacy:** the probe only hits `pypi.org` and `api.github.com` with
no authentication; it sends the installed version implicitly via
User-Agent and nothing else. If the user objects, they can opt out by
running:

```bash
touch ~/.cache/simdref-asm-skill/disabled
```

Check for this file before running the probe block; short-circuit if
present.

______________________________________________________________________

## 1. Input dispatch — always reuse the project's build system when one exists

**Rule:** do NOT invent `g++ -O3 -march=native …` when the project already specifies how its code is compiled. Wrong `-O`, wrong `-march`, wrong defines, wrong include paths → analysed asm doesn't match what ships. Resolve the compile command for a source file in this order:

### 1a. `compile_commands.json` (preferred)

Produced by CMake with `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, by Bear (`bear -- make`), or by `intercept-build`, build Release and use command line arguments to inject debug symbols do not omit frame pointer and so on.

Search order:

- `<repo>/compile_commands.json`
- `<repo>/build/compile_commands.json`, `<repo>/build-*/compile_commands.json`
- `<repo>/out/compile_commands.json`, `<repo>/cmake-build-*/compile_commands.json`
- `$CMAKE_BUILD_DIR/compile_commands.json`

If absent **but** `CMakeLists.txt` exists, offer (and ask before running):

```bash
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
```

Then read `build/compile_commands.json`.

Extract the record whose `file` matches the target source. From its `command` (or `arguments`):

- Remove `-c`.
- Remove the `-o <obj>` pair.
- Append `-S -masm=att -fverbose-asm -o /tmp/asm-analysis.s`.
- Keep everything else: `-O*`, `-march`, `-mtune`, `-std=`, `-D*`, `-I*`, `-isystem`, warnings, sysroot.

### 1b. Makefile / Ninja (no `compile_commands.json` but a build exists)

- `make -n <target>` or `ninja -t commands <target>`; grep the output for the compile line that names the source file.
- Apply the same `-c` → `-S -masm=att -fverbose-asm -o /tmp/asm-analysis.s` substitution.
- If the emitted line is unusable (recursive make, complex wrappers), offer:
  ```bash
  bear -- make <target>   # or: bear -- ninja <target>
  ```
  Then restart from step 1a.

### 1c. No build system detected — recover flags from docs/`gcc -MM`, do NOT synthesise one

If `CMakeLists.txt` exists → go to 1a (offer the `cmake -B build` step).
Else if `Makefile` / `GNUmakefile` / `build.ninja` exists → go to 1b.
Else: **do NOT synthesise a build system.** Do not run `cmake -B build` on a project that doesn't ship CMake, and do not write a `Makefile`. Instead, recover the real compile line:

1. Grep project docs for the canonical compile line:
   - `README*`, `INSTALL*`, `BUILD*`
   - `docs/`
   - `.github/workflows/*.yml`
   - `Dockerfile*`, `.devcontainer/`
   - `conanfile.*`
   - `pyproject.toml` (for extension modules), `setup.py`
1. If the project is a single-file / header-only demo, ask the user for the known-good compile line they use. Do not guess.
1. Use the compiler to enumerate what the TU needs, so a hand-compile at least gets include paths right:
   ```bash
   gcc -MM <source>          # dependencies (headers the TU actually includes)
   gcc -H <source>           # include tree
   gcc -v -E <source>        # default search paths and implicit flags
   ```

Only after the above steps fail, fall back to hand-compile. Before running, require the user to confirm each of:

- `-O` level (default suggestion: `-O3`)
- `-march` (default suggestion: probe from §2)
- any `-I` include paths
- any `-D` defines

Then:

```bash
<cxx> <flags> -S -masm=att -fverbose-asm -o /tmp/asm-analysis.s <source>
```

### 1d. Already-compiled binary / object (`.o`, `.a`, ELF)

Skip compilation entirely:

```bash
objdump -d -M att --no-show-raw-insn --demangle <obj> > /tmp/asm-analysis.s
```

### 1e. Raw `.s` input

Use as-is.

### Invariants for every path

- Output always goes to `/tmp/asm-analysis.s` - or in the build directory but never back to the source directory.
- **AT&T syntax only** — simdref's parser expects it.
- Never modify the user's build artefacts or `build/` directory beyond what `cmake -B build` creates.
- Do not create `CMakeLists.txt`, `Makefile`, or `build.ninja` for the user. If the project has no build system, the user owns that decision; we only analyse what exists.
- If the resolved compile line contains `-O0`, warn the user before proceeding — `-O0` codegen is rarely what anyone wants to analyse.

______________________________________________________________________

## 2. Microarch resolution

`simdref annotate --arch` wants a named microarch (`skylake-x`, `zen4`, …), never `native`. Resolve in order:

1. User-specified → use it.
1. Compile line has `-march=<name>` with a named microarch → use it.
1. Compile line has `-march=native` or no `-march` → probe:
   ```bash
   gcc -march=native -Q --help=target 2>/dev/null | awk '/-march=/ {print $2; exit}'
   ```
   Cross-check against `/proc/cpuinfo` "model name".
1. Still ambiguous → ask. Never silently guess.

______________________________________________________________________

## 2b. Profile-driven region selection (OPTIONAL; skips 3 when available)

If the user can run the binary and you have access to a profiler, let the
tooling pick the hot region instead of hand-selecting one. Build with
`-g -fno-omit-frame-pointer` so addr2line and perf call-graphs work.

```bash
# All-in-one: record, disassemble, annotate, detect hot loops, merge.
simdref profile run --target ./a.out --args "input.dat" \
                    --adapter perf --event "cycles:u,instructions:u" \
                    --duration 10 --arch <resolved> --top 5 -o report/
```

Artifacts in `report/`:

- `perf.data`, `disasm.s`, `annotated.json`, `samples.json`
- `loops.json` — top-N natural loops ranked by cumulative sample weight
- `hot.sa` — side-annotated listing with per-line hotness bars
- `merged.json` — per-instruction `{annotation, hotness:{event:{samples,weight,source_kind}, rank, in_hot_loop}}`
- `summary.md` — top loops + hottest instructions rollup

Read `summary.md` first, then `hot.sa` for the full inlined hot region.
Do **not** hand-edit or regex `hot.sa`; reach for `merged.json` when you
need structured data.

### Reading the output

- **Low FMA / high load-store share** (e.g. `vmovups` > `vfmadd213ps`) →
  memory-bound inner loop; suggest tiling, prefetch, or layout changes
  before touching ISA choice.
- **High `mov`/`cmp` share, no SIMD mnemonics** → scalar branch-bound
  loop; intrinsic vectorisation is on the table.
- **AVX2 `vpack*`/`vpunpck*`/`vpshuf*` chains dominating** → compiler
  auto-vectorised a scalar kernel with a costly pack/unpack shuffle
  path; a hand-written intrinsic version may beat it.

### Event names

perf event names vary by hardware. The `perf` adapter normalises
Intel hybrid-CPU PMU names (`cpu_core/cycles/u` → `cycles`,
`cpu_atom/instructions/u` → `instructions`) and strips the `:u`
/`:pp` modifier suffixes, so downstream tools can always rank with
`--event cycles` or `--event instructions`.

### Fallback paths (no perf, no root, CI containers)

```bash
simdref profile ingest --adapter mca     --input mca.json        -o samples.json
simdref profile ingest --adapter vtune   --input r000hs.csv      -o samples.json
simdref profile ingest --adapter uprof   --input uprof.csv       -o samples.json
simdref profile ingest --adapter exegesis --input exegesis.json  -o samples.json
simdref profile ingest --adapter xctrace --input trace.xml       -o samples.json  # macOS

simdref profile hotloops disasm.s samples.json --event cycles --top 3 -o loops.json
simdref profile merge    annotated.json samples.json --restrict-to loops.json \
                         --format sa -o hot.sa
```

`--adapter mca` is the universal static-only fallback — it works wherever
`llvm-mca` works and tags its output `source_kind=modeled`.

### PIE binaries and address joining

`simdref annotate` parses objdump output with `--track-positions`. The
perf adapter resolves each sample's `(sym, symoff)` through the binary's
own symbol table so address-level joins work on PIE/ASLR targets
without any manual base-offset arithmetic. Just pass `--binary <path>`
to `profile ingest` (the `profile run` wrapper does this for you).

When hot loops are known, skip §3 and annotate just the loop bodies in §4.

______________________________________________________________________

## 3. Region selection (MANDATORY above ~500 lines)

Annotating a whole TU wastes tokens and buries the answer.

- For `-S` output, extract by symbol:
  ```bash
  awk "/^<mangled>:/,/^\t\.size/" /tmp/asm-analysis.s > /tmp/asm-analysis.region.s
  ```
  Use `-ffunction-sections` to get clean boundaries if needed.
- For `objdump`, scope up-front: `objdump --disassemble=<sym>`.
- If the user hasn't named a function, ask. Do not annotate the whole file.

______________________________________________________________________

## 4. Annotate

```bash
simdref annotate /tmp/asm-analysis.s -o /tmp/asm-analysis.sa \
  --arch <resolved> \
  --format sa \
  --performance --docs \
  --unknown mark
```

Also emit structured records for downstream use:

```bash
simdref annotate /tmp/asm-analysis.s --format json -o /tmp/asm-analysis.json
```

Read the JSON; do **not** regex the `.sa` file. JSON records already carry `{mnemonic, known, summary, annotation}`.

______________________________________________________________________

## 4a. Sanity-check the annotation

`simdref annotate` can be wrong: mnemonics may be misclassified, the wrong uarch row may be joined, or measured/modeled rows may swap. Before quoting numbers in §7:

- Spot-check 2–3 of the dominant mnemonics against their primary source (Intel Intrinsics Guide, uops.info page, Arm Exploration Tools, LLVM schedule model) via `simdref show <mnemonic> --arch <resolved>` and, where feasible, the original upstream URL.
- If the annotation marks an instruction `unknown ??` that you recognise as standard, flag a catalog bug rather than silently skipping.
- If any cross-checked number disagrees with simdref's payload by more than the measurement noise floor, surface the discrepancy in §7 instead of picking one.

______________________________________________________________________

## 5. Batch-resolve mnemonics

Extract distinct `mnemonic` values from the JSON, then:

```bash
printf '%s\n' "${mnemonics[@]}" | simdref llm batch --source-kind measured --preset intel
```

Parse the NDJSON with `jq -c .`. Prefer `--source-kind measured`; fall back to `any` only for mnemonics that return `no_match` on measured.

______________________________________________________________________

## 6. Cross-check (optional but recommended)

```bash
llvm-mca -mcpu=<resolved> -iterations=100 /tmp/asm-analysis.s
```

If simdref and llvm-mca disagree on CPI by >2×, flag the disagreement to the user instead of picking a side.

______________________________________________________________________

## 7. Propose

Structured, no raw NDJSON:

1. **What** — one sentence: what the region computes.
1. **Bottleneck** — dominant mnemonics with their lat/cpi, cited from the `llm` payloads.
1. **Proposal** — concrete intrinsic or instruction swap, with the target intrinsic's signature from its payload and *why* it's faster (lower CPI, fused op, wider vector, fewer deps).
1. **Verification recipe** — exact shell steps to re-compile and re-annotate so the user can confirm.

______________________________________________________________________

## 8. Iterate

After the user applies a change, offer to re-run stages 1–5 and produce a **before/after** diff: total CPI, total latency over the region, and which specific lines changed.

______________________________________________________________________

## 9. Hard refuse rules

- `simdref annotate` reports `unknown ??` on >20% of instructions → stop, report catalog gap, do not propose replacements.
- No measured data for the resolved microarch → say so. Do not fall back to modeled without explicit user OK.
- Inline asm or hand-written `.s` → confirm with the user before suggesting changes.
- Files >5000 lines with no region specified → refuse whole-file annotation; require a symbol.
- If the spot-checks in §4a contradict simdref's payload on the dominant mnemonic, stop and report the disagreement before proposing a swap.

## 10. Simdref limitations and bug reporting

- always verify that simdref is correct and if you spot a bug, discrepancy, missing feature, missing annotation, or anything else that looks wrong, report it to the simdref team with a minimal repro and steps to verify. Do not silently work around or hand-wave simdref's output.
