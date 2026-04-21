# Changelog

All notable changes to the simdref VS Code extension will be documented in
this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - Unreleased

### Added
- Initial Marketplace preview.
- Hover and completion for SIMD intrinsics in C/C++/Objective-C/Objective-C++.
- Hover for assembly mnemonics in `.s` / `.S` / `.asm` / `.inc`.
- Managed Python virtualenv with bundled simdref server wheel — zero setup
  after install, assuming Python 3.11+ is available.
- Settings: `serverPath`, `pythonPath`, `showPerfMetrics`, `architectures`,
  `trace.server`.
- Commands: restart server, reinstall managed server, show output channel.
