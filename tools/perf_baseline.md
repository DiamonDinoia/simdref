# simdref perf baseline

All numbers captured with:

- commit `15b514a` (pre-perf work)
- catalog ≈ 117 K intrinsics + 25 K instructions
- Python 3.13, Linux x86_64, Chromium via Playwright 1.x (headless)
- server: bare `python -m http.server 8765` (no gzip, no cache)
- profiler: `tools/profile_web.py --query add`
- TUI profiler: `SIMDREF_PROFILE=1 python -m simdref.tui`

Re-run with:

```
# Web
python -m simdref web                    # export to web/
(cd web && python -m http.server 8765 &)
/tmp/pw-venv/bin/python tools/profile_web.py --query add

# TUI
PYTHONPATH=src SIMDREF_PROFILE=1 python -c "from simdref.tui import _fts_search; ..."
```

## Web — 2026-04-17 baseline

| Metric                         | Value                 |
|--------------------------------|-----------------------|
| `search-index.json` raw        | **182 MB** (189 874 420 B) |
| `search-index.json` gz (ideal) | ~6.0 MB (6 267 558 B) — **server does not emit** |
| `intrinsic-details.json` raw   | 118 MB (122 971 686 B) — lazy-loaded |
| `filter_spec.json`             | 44 KB |
| `index.html`                   | 76 KB (inlined CSS + JS) |
| Cold load → `networkidle`      | **2.62 s** |
| First paint                    | 88 ms |
| JS heap after load (used)      | **703 MB** |
| JS heap (total)                | 747 MB |
| Time-to-first-result 'a'       | 56 ms |
| Time-to-first-result 'd'       | 135 ms |
| Time-to-first-result 'd' (3rd) | **684 ms** |

Note: `networkidle` does not reflect the lazy `intrinsic-details.json` fetch;
the real perceived cold-load after the user selects an intrinsic is longer.

## TUI — 2026-04-17 baseline

`_fts_search(conn, 'add', families={Intel,Arm,RISC-V}, subs={Intel: SSE..AVX-512}, limit=20)`

| Metric                            | Value  |
|-----------------------------------|--------|
| Wall time (no profiler)           | **127 ms** |
| Wall time (with cProfile)         | 320 ms |
| `_isa_visible` cumulative         | 227 ms (71 %) |
| `display.isa_family` cumulative   | 172 ms |
| `sqlite3.execute` cumulative      | 55 ms  |

Top cProfile frames (cumulative):

```
   ncalls   tottime  cumtime  function
        1     0.002    0.319  _fts_search
    17540    0.024    0.227  _isa_visible              ← Python-side filter
    25807    0.032    0.172  display.isa_family        ← called per row
        2    0.002    0.164  _query_intrinsic_rows
    25807    0.023    0.096  display.display_isa       ← called per row
    25807    0.039    0.064  display.normalize_token
       15    0.055    0.055  sqlite3.execute
    40077    0.027    0.048  display.normalize_isa_token
   223560    0.023    0.023  str.startswith
     7135    0.004    0.021  _isa_matches_sub
```

**Headline**: ~75 % of per-keystroke cost is Python-side ISA filtering of SQL
candidates. SQL itself is 55 ms.

## Targets (from plan)

| Metric                   | Today     | Target        |
|--------------------------|-----------|---------------|
| Web cold load            | 2.6 s     | < 2 s cold / < 500 ms warm |
| Web per-keystroke p95    | ~680 ms   | < 100 ms      |
| Web JS heap              | 703 MB    | « 200 MB      |
| `search-index.json` raw  | 182 MB    | ≤ 15 MB       |
| `search-index.json` gz   | (n/a)     | ≤ 3 MB on-wire |
| TUI `_fts_search` p95    | 127 ms    | ~instant (< 40 ms) |
| TUI preset click         | visible flash | < 50 ms, no flash |
