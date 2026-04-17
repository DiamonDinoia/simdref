#!/usr/bin/env python3
"""Cold-load + keystroke profiling harness for the simdref web UI.

Reads metrics from a running static server (default: http://127.0.0.1:8765)
serving the exported ``web/`` directory, and reports:

  * cold-load wall time (navigation -> networkidle)
  * time-to-first-paint
  * time-to-first-result for each of the characters in ``--query``
  * JS heap used after load (MB)
  * total transfer size of the top-level JSON blobs

Writes a Chromium tracing zip to ``--trace`` (default ``/tmp/simdref-trace.zip``).

Usage:
    /tmp/pw-venv/bin/python tools/profile_web.py --url http://127.0.0.1:8765 \\
        --query add

Requires the venv created earlier at /tmp/pw-venv (playwright installed).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def measure_wire_sizes(base_url: str) -> dict[str, dict[str, Any]]:
    """Return raw + gzipped download sizes for the top-level JSON blobs."""
    out: dict[str, dict[str, Any]] = {}
    for name in ("search-index.json", "intrinsic-details.json", "filter_spec.json", "index.html"):
        url = f"{base_url.rstrip('/')}/{name}"
        try:
            raw = urlopen(url, timeout=30).read()
            req = Request(url, headers={"Accept-Encoding": "gzip"})
            with urlopen(req, timeout=30) as resp:
                body = resp.read()
                gz = resp.headers.get("Content-Encoding") == "gzip"
            out[name] = {"raw_bytes": len(raw), "gz_bytes": len(body) if gz else -1}
        except Exception as exc:  # pragma: no cover
            out[name] = {"error": str(exc)}
    return out


async def run(url: str, query: str, trace_path: Path, headed: bool) -> dict:
    from playwright.async_api import async_playwright

    report: dict = {"url": url, "query": query}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not headed)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await ctx.tracing.start(screenshots=True, snapshots=True)

        t0 = time.perf_counter()
        await page.goto(url, wait_until="networkidle", timeout=120_000)
        report["t_networkidle_s"] = round(time.perf_counter() - t0, 3)

        # Time to first paint via Performance API
        fp = await page.evaluate(
            "() => { const p = performance.getEntriesByType('paint'); "
            "const f = p.find(e => e.name === 'first-paint'); "
            "return f ? f.startTime : null; }"
        )
        report["first_paint_ms"] = round(fp, 1) if fp else None

        # JS heap after load
        metrics = await page.evaluate(
            "() => performance.memory ? {used: performance.memory.usedJSHeapSize, "
            "total: performance.memory.totalJSHeapSize} : null"
        )
        if metrics:
            report["heap_used_mb"] = round(metrics["used"] / 1e6, 1)
            report["heap_total_mb"] = round(metrics["total"] / 1e6, 1)

        # Focus the search box.
        await page.click("#query")

        per_key: list[dict] = []
        for ch in query:
            t1 = time.perf_counter()
            await page.keyboard.type(ch, delay=0)
            try:
                # Wait for at least one result or a "0 results" signal.
                await page.wait_for_function(
                    "() => document.querySelectorAll('.result, .result-row, [data-result]').length > 0 "
                    "|| /0\\s*result/i.test(document.body.textContent || '')",
                    timeout=10_000,
                )
                dt = (time.perf_counter() - t1) * 1000
            except Exception as exc:  # pragma: no cover
                dt = -1.0
                per_key.append({"ch": ch, "error": str(exc)})
                continue
            per_key.append({"ch": ch, "first_result_ms": round(dt, 1)})
            await asyncio.sleep(0.15)
        report["per_keystroke"] = per_key

        await ctx.tracing.stop(path=str(trace_path))
        await browser.close()

    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--query", default="add")
    ap.add_argument("--trace", type=Path, default=Path("/tmp/simdref-trace.zip"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args()

    sizes = measure_wire_sizes(args.url)
    report = asyncio.run(run(args.url, args.query, args.trace, args.headed))
    report["wire_sizes"] = sizes

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"URL:               {report['url']}")
    print(f"cold load (net.idle): {report.get('t_networkidle_s')} s")
    print(f"first-paint:          {report.get('first_paint_ms')} ms")
    print(f"JS heap (used/total): {report.get('heap_used_mb')} / {report.get('heap_total_mb')} MB")
    print("per-keystroke time-to-first-result:")
    for entry in report.get("per_keystroke", []):
        if "error" in entry:
            print(f"  '{entry['ch']}': ERROR {entry['error']}")
        else:
            print(f"  '{entry['ch']}': {entry['first_result_ms']} ms")
    print("wire sizes (raw | gz when Accept-Encoding: gzip honoured):")
    for name, info in sizes.items():
        if "error" in info:
            print(f"  {name}: ERROR {info['error']}")
        else:
            raw = info["raw_bytes"]
            gz = info["gz_bytes"]
            print(f"  {name}: raw={raw:>12,} B   gz={'n/a' if gz < 0 else f'{gz:,} B'}")
    print(f"trace: {args.trace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
