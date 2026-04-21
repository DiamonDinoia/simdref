# simdref for Visual Studio Code

Hover documentation, completion, and microarchitecture performance data for
SIMD intrinsics and assembly mnemonics across **x86 (SSE/AVX/AVX-512)**,
**Arm Neon/SVE**, and **RISC-V V**.

Powered by the open-source [simdref](https://github.com/DiamonDinoia/simdref)
catalog (intrinsic guides, vendor manuals, LLVM scheduling models, and
published measurements).

## Features

- **Hover** — signature, description, ISA, linked instructions, and best
  measured latency / cycles-per-instruction for any intrinsic or mnemonic.
- **Completion** — FTS5-backed candidate list for `_mm`, `__riscv_v`,
  `vaddq_`, and friends.
- **Assembly support** — hover over `vaddps`, `fma.vv`, `fmla.4s` in `.s`
  and `.S` files.
- **Configurable** — hide performance metrics, pin to a single architecture,
  bring your own server binary.

## Install

1. Install from the VS Code Marketplace (or `code --install-extension
   simdref-<version>.vsix`).
2. On first activation the extension creates a managed virtualenv under the
   extension's global-storage directory and installs the bundled simdref
   Python wheel into it. **Requires Python 3.11 or newer on your PATH.**
3. Open any C, C++, Objective-C/C++, or assembly file and hover an
   intrinsic name or mnemonic.

If Python is not found the extension surfaces an actionable error pointing
to [python.org/downloads](https://www.python.org/downloads/).

## Settings

| Setting                  | Default | Description                                                                                 |
| ------------------------ | ------- | ------------------------------------------------------------------------------------------- |
| `simdref.serverPath`     | `""`    | Explicit path to `simdref-lsp`. Bypasses the managed venv.                                  |
| `simdref.pythonPath`     | `""`    | Interpreter used to bootstrap the managed venv. Defaults to `python3` / `python` on PATH.   |
| `simdref.showPerfMetrics`| `true`  | Include best latency and cycles/instr in hover.                                             |
| `simdref.architectures`  | `[]`    | Restrict hover perf rows to the listed archs (`"x86"`, `"arm"`, `"riscv"`). Empty = all.    |
| `simdref.trace.server`   | `"off"` | LSP protocol trace in the simdref output channel.                                           |

## Commands

- **simdref: Restart Server** — stop and re-launch the language server.
- **simdref: Reinstall Python Server** — wipe the managed venv and reinstall
  the bundled wheel. Useful after a VS Code/OS upgrade.
- **simdref: Show Output** — open the simdref output channel (install
  logs, server stderr).

## Compatibility

- VS Code `>= 1.85.0`
- Python `>= 3.11` (only the interpreter; the extension installs all other
  Python dependencies into its managed venv).

## License

GPL-3.0-or-later. The catalog data retains its upstream licences (see
[docs/SOURCES.md](https://github.com/DiamonDinoia/simdref/blob/main/docs/SOURCES.md)).
