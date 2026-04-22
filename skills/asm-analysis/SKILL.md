---
name: asm-analysis
description: |
  Use when the user asks you to look at, analyse, profile, or optimise
  ASSEMBLY — whether the starting point is a C/C++ source file, a CMake
  target, a Makefile target, a compiled object file, a binary, or a raw
  .s listing. Drives a compile → objdump/-S → simdref annotate → simdref
  llm batch pipeline and proposes intrinsic-level replacements with cited
  latency/CPI.

  TRIGGER when the user says things like: "look at the asm", "inspect
  codegen", "why is this loop slow", "can this be vectorised", "what
  intrinsic would replace this", "annotate this .s file", "profile this
  hot path at the instruction level".

  DO NOT TRIGGER for pure source-level refactoring, style changes, or
  questions about C++ semantics that don't require reading generated code.
---

# asm-analysis

Pipeline: **input → compile line → `.s` → `simdref annotate` → `simdref llm batch` → proposal**. Every stage below is mandatory unless it says otherwise.

---

## 0. Preflight (runs once before any pipeline stage)

Check `simdref --version`. If it fails, ask the user which of these two to use, then proceed:

1. **Persistent install via pipx:**
   ```bash
   pipx install simdref --pip-args "--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/"
   ```
2. **Transient per-invocation via uvx (no install state):**
   ```bash
   uvx --index https://test.pypi.org/simple/ --index https://pypi.org/simple/ --index-strategy unsafe-best-match --from simdref simdref <args...>
   ```
   In this mode, prepend the `uvx …` prefix to every `simdref …` invocation in the rest of this skill.

No version pin — both resolve the latest TestPyPI release. Extra-index to real PyPI is required because `simdref`'s runtime deps (httpx, typer, textual, rich, rapidfuzz, msgpack, PyYAML, pdfplumber, PyMuPDF) are only on real PyPI.

Never install without asking.

---

## 1. Input dispatch — always reuse the project's build system when one exists

**Rule:** do NOT invent `g++ -O2 -march=native …` when the project already specifies how its code is compiled. Wrong `-O`, wrong `-march`, wrong defines, wrong include paths → analysed asm doesn't match what ships. Resolve the compile command for a source file in this order:

### 1a. `compile_commands.json` (preferred)

Produced by CMake with `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, by Bear (`bear -- make`), or by `intercept-build`.

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

### 1c. No build system detected — try to *create* one before hand-compiling

If `CMakeLists.txt` exists → go to 1a (offer the `cmake -B build` step).
Else if `Makefile` / `GNUmakefile` / `build.ninja` exists → go to 1b.
Else if the file is a standalone TU with no includes outside the standard library → **last resort: hand-compile**, but before running, ask the user for:
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

- Output always goes to `/tmp/asm-analysis.s` — never inside the user's repo.
- **AT&T syntax only** — simdref's parser expects it.
- Never modify the user's build artefacts or `build/` directory beyond what `cmake -B build` creates.
- If the resolved compile line contains `-O0`, warn the user before proceeding — `-O0` codegen is rarely what anyone wants to analyse.

---

## 2. Microarch resolution

`simdref annotate --arch` wants a named microarch (`skylake-x`, `zen4`, …), never `native`. Resolve in order:

1. User-specified → use it.
2. Compile line has `-march=<name>` with a named microarch → use it.
3. Compile line has `-march=native` or no `-march` → probe:
   ```bash
   gcc -march=native -Q --help=target 2>/dev/null | awk '/-march=/ {print $2; exit}'
   ```
   Cross-check against `/proc/cpuinfo` "model name".
4. Still ambiguous → ask. Never silently guess.

---

## 3. Region selection (MANDATORY above ~500 lines)

Annotating a whole TU wastes tokens and buries the answer.

- For `-S` output, extract by symbol:
  ```bash
  awk "/^<mangled>:/,/^\t\.size/" /tmp/asm-analysis.s > /tmp/asm-analysis.region.s
  ```
  Use `-ffunction-sections` to get clean boundaries if needed.
- For `objdump`, scope up-front: `objdump --disassemble=<sym>`.
- If the user hasn't named a function, ask. Do not annotate the whole file.

---

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

---

## 5. Batch-resolve mnemonics

Extract distinct `mnemonic` values from the JSON, then:
```bash
printf '%s\n' "${mnemonics[@]}" | simdref llm batch --source-kind measured --preset intel
```

Parse the NDJSON with `jq -c .`. Prefer `--source-kind measured`; fall back to `any` only for mnemonics that return `no_match` on measured.

---

## 6. Cross-check (optional but recommended)

```bash
llvm-mca -mcpu=<resolved> -iterations=100 /tmp/asm-analysis.s
```

If simdref and llvm-mca disagree on CPI by >2×, flag the disagreement to the user instead of picking a side.

---

## 7. Propose

Structured, no raw NDJSON:
1. **What** — one sentence: what the region computes.
2. **Bottleneck** — dominant mnemonics with their lat/cpi, cited from the `llm` payloads.
3. **Proposal** — concrete intrinsic or instruction swap, with the target intrinsic's signature from its payload and *why* it's faster (lower CPI, fused op, wider vector, fewer deps).
4. **Verification recipe** — exact shell steps to re-compile and re-annotate so the user can confirm.

---

## 8. Iterate

After the user applies a change, offer to re-run stages 1–5 and produce a **before/after** diff: total CPI, total latency over the region, and which specific lines changed.

---

## 9. Hard refuse rules

- `simdref annotate` reports `unknown ??` on >20% of instructions → stop, report catalog gap, do not propose replacements.
- No measured data for the resolved microarch → say so. Do not fall back to modeled without explicit user OK.
- Inline asm or hand-written `.s` → confirm with the user before suggesting changes.
- Files >5000 lines with no region specified → refuse whole-file annotation; require a symbol.
