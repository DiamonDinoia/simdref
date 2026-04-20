"""Command-line interface for simdref.

This module defines the Typer application, its maintenance/export commands,
and the smart bare-word lookup that fires when no recognised subcommand is
given.

Display and formatting logic lives in :mod:`simdref.display`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from contextlib import nullcontext as _nullcontext
from dataclasses import asdict
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer.core import TyperGroup

# Stderr-only Console for bootstrap/download status. Must not share stdout with
# `simdref llm` payloads, otherwise callers that json.loads(stdout) break.
err_console = Console(stderr=True)

from simdref.display import (
    console,
    display_architecture,
    display_isa,
    instruction_query_text,
    instruction_variant_items,
    isa_sort_key,
    isa_visible,
    normalize_instruction_query,
    render_intrinsic,
    render_instruction,
    render_instruction_variants,
    render_search_results,
)
from simdref import __version__
from simdref.ingest import build_catalog
from simdref.ingest_sources import refresh_local_arm_intrinsics_bundle
from simdref.manpages import open_manpage, write_manpages
from simdref.perf import variant_perf_summary
from simdref.queries import intrinsic_perf_summary_runtime, instruction_rows_for_intrinsic
from simdref.search import SearchResult, find_intrinsic, find_instructions, search_catalog, search_records
from simdref.storage import (
    CATALOG_PATH,
    DATA_DIR,
    DEFAULT_MAN_DIR,
    SQLITE_PATH,
    SQLITE_SCHEMA_VERSION,
    WEB_DIR,
    build_sqlite,
    load_catalog,
    load_instruction_from_db,
    load_intrinsic_from_db,
    load_instructions_by_mnemonic_from_db,
    load_instructions_by_mnemonic_prefix_from_db,
    open_db,
    save_catalog,
    search_instruction_candidates_from_db,
    search_intrinsic_candidates_from_db,
    sqlite_schema_is_current,
)
from simdref.web import export_web


def _run_tui(*, initial_query: str = "", initial_preset: str | None = None):
    from simdref.tui import run_tui

    return run_tui(initial_query=initial_query, initial_preset=initial_preset)


class SimdrefGroup(TyperGroup):
    """Top-level CLI group with usage that reflects bare-query mode."""

    def collect_usage_pieces(self, ctx):  # type: ignore[override]
        return ["[OPTIONS] [QUERY] | COMMAND [ARGS]..."]


app = typer.Typer(
    cls=SimdrefGroup,
    help=(
        "Local SIMD reference across Intel intrinsics, instruction data, performance measurements, and SDM-derived descriptions.\n\n"
        "Run without arguments to open the TUI. Pass a bare query to search or open matching results directly.\n\n"
        "Common rebuild commands:\n"
        "  simdref update --build-local    Full local rebuild from upstream sources.\n"
        "  simdref update --offline        Local rebuild from bundled fixtures only.\n"
        "  simdref update --build-local --with-sdm\n"
        "                                  Heaviest local rebuild, including Intel SDM parsing."
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
)
SHOW_FP16_ISAS = False
SHORT_MODE = False
FULL_MODE = False


@contextmanager
def _pager_context():
    """Pipe Rich output through a pager that handles ANSI colors."""
    pager_cmd = os.environ.get("PAGER", "")
    less = shutil.which("less")
    if less and ("less" in pager_cmd or not pager_cmd):
        # Use less -RFX: Raw ANSI, quit-if-one-screen, no-init
        from rich.pager import Pager

        class _LessPager(Pager):
            def show(self, content: str) -> None:
                proc = subprocess.Popen(
                    [less, "-RFX"],
                    stdin=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                )
                try:
                    proc.communicate(input=content)
                except KeyboardInterrupt:
                    proc.kill()

        yield console.pager(pager=_LessPager(), styles=True)
    else:
        # Fallback: Rich's default pager without styles (safe)
        yield console.pager(styles=False)

GITHUB_REPO = "DiamonDinoia/simdref"
RELEASE_TAG = "data-latest"


# ---------------------------------------------------------------------------
# Release download helpers
# ---------------------------------------------------------------------------


def _release_tag_candidates() -> list[str]:
    version_tag = f"data-v{__version__}-schema{SQLITE_SCHEMA_VERSION}"
    schema_tag = f"data-schema{SQLITE_SCHEMA_VERSION}-latest"
    return [version_tag, schema_tag, RELEASE_TAG]


def _release_asset_url(tag: str, asset_name: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{asset_name}"


def _download_from_release() -> None:
    """Download pre-built catalog and database from GitHub Release."""
    from simdref.storage import ensure_dir

    ensure_dir(DATA_DIR)

    for asset in ("catalog.msgpack", "catalog.db"):
        dest = DATA_DIR / asset
        for tag in _release_tag_candidates():
            url = _release_asset_url(tag, asset)
            err_console.print(f"downloading {asset} from {tag}...", style="dim")
            try:
                with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 64):
                            f.write(chunk)
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                err_console.print(f"failed to download {asset}: {exc.response.status_code}", style="red")
                raise typer.Exit(code=1) from exc
        else:
            err_console.print(f"failed to download {asset}: no compatible release asset found", style="red")
            err_console.print("try 'simdref update --build-local' to build locally", style="yellow")
            raise typer.Exit(code=1)
    err_console.print("download complete", style="green")


def _build_runtime_locally(*, offline: bool, man_dir: Path, include_sdm: bool = False) -> None:
    """Build catalog, SQLite, manpages, and web bundle locally."""
    interactive_progress = console.is_terminal and os.environ.get("GITHUB_ACTIONS") != "true"

    if not interactive_progress:
        def _status(msg: str) -> None:
            err_console.print(msg, style="dim")

        if not offline:
            _status("Refreshing local Arm intrinsics cache")
            try:
                written = refresh_local_arm_intrinsics_bundle()
                _status(f"Refreshed {len(written)} Arm JSON files in {written[0].parent}")
            except Exception as exc:
                _status(f"Arm JSON refresh failed, using cached local files: {exc}")
        _status("Building local catalog")
        catalog = build_catalog(offline=offline, include_sdm=include_sdm, status=_status)
        _status("Saving catalog snapshot")
        save_catalog(catalog)
        _status("Building SQLite search database")
        build_sqlite(catalog)
        _status("Writing manpages")
        write_manpages(catalog, man_dir)
        _status("Exporting static web bundle")
        export_web(catalog, WEB_DIR)
        err_console.print(
            f"updated catalog with {len(catalog.intrinsics)} intrinsics and {len(catalog.instructions)} instructions",
            style="green",
        )
        return

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=err_console,
    )
    with progress:
        task = progress.add_task("Building local catalog", total=4)

        def _status(msg: str) -> None:
            progress.update(task, description=msg)

        if not offline:
            progress.update(task, description="Refreshing local Arm intrinsics cache")
            try:
                refresh_local_arm_intrinsics_bundle()
            except Exception as exc:
                progress.update(task, description=f"Arm JSON refresh failed, using cached files: {exc}")
        catalog = build_catalog(offline=offline, include_sdm=include_sdm, status=_status)
        progress.advance(task, 1)

        progress.update(task, description="Saving catalog snapshot")
        save_catalog(catalog)
        progress.advance(task, 1)

        progress.update(task, description="Building SQLite search database")
        build_sqlite(catalog)
        progress.advance(task, 1)

        progress.update(task, description="Writing manpages and exporting static web bundle")
        write_manpages(catalog, man_dir)
        export_web(catalog, WEB_DIR)
        progress.advance(task, 1)

        progress.update(task, description="Local build complete")

    err_console.print(
        f"updated catalog with {len(catalog.intrinsics)} intrinsics and {len(catalog.instructions)} instructions",
        style="green",
    )


def _refresh_runtime_from_existing_catalog(*, man_dir: Path) -> None:
    """Rebuild derived runtime artifacts from the local msgpack snapshot.

    This is substantially cheaper than a full local source rebuild and is
    sufficient when the catalog is already present but the SQLite schema,
    manpages, or web export need to be refreshed.
    """
    if not CATALOG_PATH.exists():
        raise typer.Exit(code=1)
    catalog = load_catalog()
    build_sqlite(catalog)
    write_manpages(catalog, man_dir)
    export_web(catalog, WEB_DIR)
    err_console.print(
        f"refreshed runtime from existing catalog with {len(catalog.intrinsics)} intrinsics and {len(catalog.instructions)} instructions",
        style="green",
    )


def _finalize_runtime_from_download(*, man_dir: Path) -> None:
    """Refresh local derived artifacts after downloading release assets."""
    if not CATALOG_PATH.exists():
        raise typer.Exit(code=1)
    catalog = load_catalog()
    write_manpages(catalog, man_dir)
    export_web(catalog, WEB_DIR)
    err_console.print(
        f"refreshed local web/man assets from downloaded catalog with {len(catalog.intrinsics)} intrinsics and {len(catalog.instructions)} instructions",
        style="green",
    )


def _download_release_or_fallback(*, man_dir: Path, fallback_offline: bool = True) -> None:
    """Prefer pre-built assets; optionally fall back to fixtures."""
    try:
        _download_from_release()
        if sqlite_schema_is_current():
            _finalize_runtime_from_download(man_dir=man_dir)
            return
        if CATALOG_PATH.exists():
            err_console.print("downloaded catalog is usable but SQLite is stale; rebuilding runtime locally from the downloaded catalog", style="yellow")
            _refresh_runtime_from_existing_catalog(man_dir=man_dir)
            return
        err_console.print("downloaded runtime schema is not current; falling back to bundled fixtures", style="yellow")
    except typer.Exit:
        if CATALOG_PATH.exists():
            err_console.print("download failed; refreshing runtime from the existing local catalog", style="yellow")
            _refresh_runtime_from_existing_catalog(man_dir=man_dir)
            return
        if not fallback_offline:
            raise
        err_console.print("falling back to bundled fixtures", style="yellow")

    _build_runtime_locally(offline=True, man_dir=man_dir)


# ---------------------------------------------------------------------------
# Catalog / runtime helpers
# ---------------------------------------------------------------------------


def _bootstrap_interactive() -> None:
    """Bootstrap runtime data with a lightweight default path."""
    err_console.print("\n[bold]No catalog found.[/bold] Downloading pre-built data if available...\n")
    _download_release_or_fallback(man_dir=DEFAULT_MAN_DIR, fallback_offline=True)


def ensure_catalog():
    """Load (or bootstrap) the in-memory catalog."""
    if not CATALOG_PATH.exists():
        _bootstrap_interactive()
    return load_catalog()


def ensure_runtime() -> None:
    """Ensure catalog + SQLite are present and current."""
    if not CATALOG_PATH.exists():
        _bootstrap_interactive()
        return
    if not sqlite_schema_is_current():
        err_console.print("runtime schema is missing or out of date; rebuilding derived runtime artifacts from the local catalog", style="yellow")
        _refresh_runtime_from_existing_catalog(man_dir=DEFAULT_MAN_DIR)


def _catalog_meta(catalog) -> dict:
    return {
        "generated_at": catalog.generated_at,
        "source_versions": [asdict(source) for source in catalog.sources],
    }



def _search_runtime(conn, query: str, limit: int = 20) -> tuple[list[SearchResult], dict[str, object], dict[str, object]]:
    candidate_limit = max(limit * 6, 60)
    intrinsics = search_intrinsic_candidates_from_db(conn, query, limit=candidate_limit)
    instructions = search_instruction_candidates_from_db(conn, query, limit=candidate_limit)
    results = search_records(intrinsics, instructions, query, limit=limit)
    intrinsic_map = {item.name: item for item in intrinsics}
    instruction_map = {item.db_key: item for item in instructions}
    return results, intrinsic_map, instruction_map


# ---------------------------------------------------------------------------
# Instruction lookup helpers
# ---------------------------------------------------------------------------


def _select_instruction_variant(catalog, query: str, items):
    parts = query.split()
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    base_query = " ".join(parts[:-1]).strip()
    if not base_query:
        return None
    index = int(parts[-1])
    if index < 1:
        return None
    if items:
        variants = instruction_variant_items(items)
    elif catalog is not None:
        variants = instruction_variant_items(find_instructions(catalog, base_query))
    else:
        variants = instruction_variant_items(_find_instructions_fast(base_query))
    if 1 <= index <= len(variants):
        return variants[index - 1]
    return None


def _find_instructions_fast(query: str):
    ensure_runtime()
    with open_db() as conn:
        exact = load_instruction_from_db(conn, query)
        if exact is not None:
            return [exact]
        parts = query.split()
        mnemonic = parts[0] if parts else query
        candidates = load_instructions_by_mnemonic_from_db(conn, mnemonic)
        if not candidates:
            return []
        normalized_query = normalize_instruction_query(query)
        exact_candidates = [
            item
            for item in candidates
            if normalize_instruction_query(item.key) == normalized_query
            or normalize_instruction_query(instruction_query_text(item)) == normalized_query
            or item.mnemonic.casefold() == query.casefold()
        ]
        if exact_candidates:
            return exact_candidates
        if mnemonic.casefold() == query.casefold():
            return candidates
        return []


def _find_instruction_family_fast(query: str):
    ensure_runtime()
    token = (query.split()[0] if query.split() else query).strip()
    if not token:
        return []
    with open_db() as conn:
        candidates = load_instructions_by_mnemonic_prefix_from_db(conn, token)
    exact_mnemonic = {item.mnemonic.casefold() for item in candidates}
    if token.casefold() in exact_mnemonic:
        return []
    return candidates


# ---------------------------------------------------------------------------
# LLM / JSON payload builders
# ---------------------------------------------------------------------------


def _resolve_query_payload(catalog, query: str, limit: int = 8) -> dict:
    intrinsic = find_intrinsic(catalog, query)
    if intrinsic is not None:
        return {
            "query": query,
            "mode": "exact",
            "match_kind": "intrinsic",
            "intrinsic": asdict(intrinsic),
            "performance": instruction_rows_for_intrinsic(catalog, intrinsic),
            **_catalog_meta(catalog),
        }
    instructions = find_instructions(catalog, query)
    if instructions:
        return {
            "query": query,
            "mode": "exact",
            "match_kind": "instruction",
            "instructions": [asdict(item) | {"key": item.key} for item in instructions],
            **_catalog_meta(catalog),
        }
    return {
        "query": query,
        "mode": "search",
        "match_kind": None,
        "results": [asdict(result) for result in search_catalog(catalog, query, limit=limit)],
        **_catalog_meta(catalog),
    }


def _llm_result_payload(conn, result: SearchResult, intrinsic_map: dict[str, object], instruction_map: dict[str, object]) -> dict:
    if result.kind == "intrinsic":
        item = intrinsic_map.get(result.key)
        if item is None:
            item = load_intrinsic_from_db(conn, result.key)
            if item is not None:
                intrinsic_map[result.key] = item
        if item is not None:
            lat, cpi = intrinsic_perf_summary_runtime(conn, item, instruction_map)
            return {
                "query": item.name,
                "intrinsic": item.name,
                "signature": item.signature,
                "instructions": item.instructions,
                "instruction_refs": item.instruction_refs,
                "summary": item.description,
                "isa": item.isa,
                "lat": lat,
                "cpi": cpi,
            }
    item = instruction_map.get(result.key)
    if item is None:
        item = load_instruction_from_db(conn, result.key)
        if item is not None:
            instruction_map[result.key] = item
    if item is not None:
        lat, cpi = variant_perf_summary(item.arch_details)
        return {
            "query": item.key,
            "intrinsic": item.linked_intrinsics,
            "summary": item.summary,
            "isa": item.isa,
            "lat": lat,
            "cpi": cpi,
        }
    return {"query": result.title, "intrinsic": [], "summary": result.subtitle, "isa": [], "lat": "-", "cpi": "-"}


def _llm_intrinsic_payload(conn, intrinsic) -> dict:
    instruction_map: dict[str, object] = {}
    lat, cpi = intrinsic_perf_summary_runtime(conn, intrinsic, instruction_map)
    return {
        "query": intrinsic.name,
        "intrinsic": intrinsic.name,
        "signature": intrinsic.signature,
        "url": intrinsic.url,
        "instructions": intrinsic.instructions,
        "instruction_refs": intrinsic.instruction_refs,
        "isa": intrinsic.isa,
        "lat": lat,
        "cpi": cpi,
        "summary": intrinsic.description,
    }


def _llm_instruction_payload(item) -> dict:
    lat, cpi = variant_perf_summary(item.arch_details)
    return {
        "query": item.key,
        "intrinsic": item.linked_intrinsics,
        "isa": item.isa,
        "lat": lat,
        "cpi": cpi,
        "summary": item.summary,
    }


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------


def _print_search_results_runtime(conn, query: str, limit: int = 20) -> None:
    results, intrinsic_map, instruction_map = _search_runtime(conn, query, limit=limit)
    prepared_rows = []
    for result in results:
        arch = "-"
        isa = "-"
        lat = "-"
        cpi = "-"
        isa_sort = (99, "-")
        if result.kind == "instruction":
            item = instruction_map.get(result.key)
            if item is not None:
                if not isa_visible(item.isa, show_fp16=SHOW_FP16_ISAS):
                    continue
                arch = display_architecture(item.architecture)
                isa = display_isa(item.isa)
                isa_sort = isa_sort_key(item.isa)
                lat, cpi = variant_perf_summary(item.arch_details)
        elif result.kind == "intrinsic":
            item = intrinsic_map.get(result.key)
            if item is not None:
                if not isa_visible(item.isa, show_fp16=SHOW_FP16_ISAS):
                    continue
                arch = display_architecture(item.architecture)
                isa = display_isa(item.isa)
                isa_sort = isa_sort_key(item.isa)
                lat, cpi = intrinsic_perf_summary_runtime(conn, item, instruction_map)
        prepared_rows.append((result, arch, isa, lat, cpi, isa_sort))
    prepared_rows.sort(key=lambda row: (row[0].kind != "instruction", row[5], row[0].title.casefold(), row[0].key.casefold()))
    render_search_results([(r, arch, isa, lat, cpi) for r, arch, isa, lat, cpi, _ in prepared_rows])


# ---------------------------------------------------------------------------
# Smart lookup (bare-word query)
# ---------------------------------------------------------------------------


def _smart_lookup(query: str, preset: str | None = None) -> int:
    """Open the TUI pre-filled with the given query."""
    ensure_runtime()
    return _run_tui(initial_query=query, initial_preset=preset)


def _is_completion_invocation(env: dict[str, str] | None = None) -> bool:
    env = env or os.environ
    for key, value in env.items():
        if not key.endswith("_COMPLETE"):
            continue
        if "SIMDREF" not in key.upper():
            continue
        if value:
            return True
    return False


# ---------------------------------------------------------------------------
# Typer commands
# ---------------------------------------------------------------------------


@app.command()
def update(
    offline: bool = typer.Option(False, help="Build locally from bundled fixtures instead of downloading pre-built data."),
    from_release: bool = typer.Option(False, "--from-release", help="Download pre-built data from GitHub Release."),
    build_local: bool = typer.Option(False, "--build-local", help="Build locally from upstream sources. This uses substantially more RAM than the default download path."),
    with_sdm: bool = typer.Option(False, "--with-sdm", help="Also parse the Intel SDM PDF for descriptions and page references. This is the heaviest local-build path and is mainly intended for CI/release generation."),
    man_dir: Path = typer.Option(DEFAULT_MAN_DIR, help="Target man root directory."),
) -> None:
    """Refresh runtime data.

    Default behavior downloads the pre-built release assets. Use
    ``--build-local`` for a full local rebuild, or ``--offline`` for the
    bundled fixture dataset.
    """
    if offline and from_release:
        raise typer.BadParameter("--offline cannot be combined with --from-release")
    if offline and build_local:
        raise typer.BadParameter("--offline already implies a local fixture build; do not combine it with --build-local")
    if with_sdm and not build_local:
        raise typer.BadParameter("--with-sdm requires --build-local")

    if from_release:
        _download_from_release()
        _finalize_runtime_from_download(man_dir=man_dir)
        return

    if not offline and not build_local:
        _download_release_or_fallback(man_dir=man_dir, fallback_offline=True)
        return

    _build_runtime_locally(offline=offline, man_dir=man_dir, include_sdm=with_sdm)


LLM_EXIT_MATCH = 0
LLM_EXIT_USAGE = 1
LLM_EXIT_NO_MATCH = 2
LLM_EXIT_AMBIGUOUS = 3
LLM_EXIT_INTERNAL = 10


def _resolve_preset_filters(preset: str | None) -> tuple[list[str] | None, list[str] | None]:
    """Translate a preset name into (isa_families, categories) overrides.

    Presets supply ISA-family + sub-ISA facets; we map them to the coarse
    ISA-family list the llm filter uses. Categories are not implied by a
    preset (they come from --filter / --category).
    """
    if not preset:
        return None, None
    from simdref.filters import ARCH_PRESETS
    spec = ARCH_PRESETS.get(preset)
    if spec is None:
        return None, None
    return sorted(spec.families), None


def _llm_filter_records(records: list[dict], isa: list[str] | None, category: list[str] | None) -> list[dict]:
    """Filter llm payload dicts by ISA family and category facets."""
    from simdref.display import isa_family as _isa_family
    if not isa and not category:
        return records
    isa_set = {f.strip() for f in (isa or []) if f and f.strip()}
    cat_set = {c.strip() for c in (category or []) if c and c.strip()}
    kept: list[dict] = []
    for rec in records:
        if isa_set:
            rec_isa = rec.get("isa") or []
            if isinstance(rec_isa, str):
                rec_isa = [rec_isa]
            families = {_isa_family(v) for v in rec_isa}
            if not families & isa_set:
                continue
        if cat_set:
            rec_cat = rec.get("category", "")
            if rec_cat not in cat_set:
                continue
        kept.append(rec)
    return kept


def _llm_format_markdown(payload: dict) -> str:
    """Render an llm payload as prompt-friendly markdown."""
    mode = payload.get("mode", "search")
    query = payload.get("query", "")
    lines: list[str] = [f"# simdref: {query}", ""]
    if mode == "exact" and payload.get("match_kind") == "intrinsic":
        rec = payload.get("result", {})
        lines.append(f"**Intrinsic:** `{rec.get('intrinsic', '')}`")
        if rec.get("signature"):
            lines.append(f"**Signature:** `{rec['signature']}`")
        if rec.get("isa"):
            lines.append(f"**ISA:** {', '.join(rec['isa'])}")
        if rec.get("instructions"):
            lines.append(f"**Instruction:** `{rec['instructions'][0]}`")
        if rec.get("lat") and rec["lat"] != "-":
            lines.append(f"**Latency:** {rec['lat']}  •  **CPI:** {rec.get('cpi', '-')}")
        if rec.get("summary"):
            lines += ["", rec["summary"]]
        return "\n".join(lines)
    items = payload.get("results", [])
    if mode == "exact":
        lines.append(f"**{len(items)} instruction match(es)**")
    else:
        lines.append(f"**{len(items)} search result(s)**")
    lines.append("")
    for r in items:
        title = r.get("intrinsic") or r.get("query") or ""
        if isinstance(title, list):
            title = ", ".join(title)
        summary = r.get("summary", "")
        isa = ", ".join(r.get("isa") or [])
        lines.append(f"- **{title}** `{isa}` — {summary}")
    return "\n".join(lines)


def _emit_llm_payload(payload: dict, fmt: str) -> None:
    if fmt == "ndjson":
        mode = payload.get("mode")
        if mode == "exact" and "result" in payload:
            typer.echo(json.dumps(payload["result"], sort_keys=True))
            return
        for item in payload.get("results") or []:
            typer.echo(json.dumps(item, sort_keys=True))
        return
    if fmt == "markdown":
        typer.echo(_llm_format_markdown(payload))
        return
    typer.echo(json.dumps(payload, sort_keys=True, indent=2))


def _llm_exit_code(payload: dict) -> int:
    mode = payload.get("mode")
    if mode == "exact":
        if "result" in payload:
            return LLM_EXIT_MATCH
        results = payload.get("results") or []
        if len(results) > 1 and payload.get("match_kind") == "instruction":
            exact_name_hits = sum(1 for r in results if r.get("query", "").casefold() == payload.get("query", "").casefold())
            if exact_name_hits > 1:
                return LLM_EXIT_AMBIGUOUS
        return LLM_EXIT_MATCH if results else LLM_EXIT_NO_MATCH
    return LLM_EXIT_MATCH if payload.get("results") else LLM_EXIT_NO_MATCH


def _llm_schema_payload() -> dict:
    """Approximate JSON Schema for llm payloads (stable for tool consumers)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "simdref.llm",
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "mode": {"type": "string", "enum": ["exact", "search"]},
            "match_kind": {"type": ["string", "null"], "enum": ["intrinsic", "instruction", None]},
            "result": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "intrinsic": {"type": ["string", "array"]},
                    "signature": {"type": "string"},
                    "instructions": {"type": "array", "items": {"type": "string"}},
                    "isa": {"type": "array", "items": {"type": "string"}},
                    "lat": {"type": "string"},
                    "cpi": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
            "results": {"type": "array", "items": {"$ref": "#/properties/result"}},
        },
        "required": ["query", "mode"],
    }


llm_app = typer.Typer(help="Structured output for LLM/tool consumption.", invoke_without_command=False)


def _llm_query_impl(
    query_tokens: list[str],
    limit: int,
    fmt: str,
    isa: list[str] | None,
    category: list[str] | None,
    preset: str | None = None,
) -> None:
    fmt_lower = (fmt or "json").lower()
    if fmt_lower not in {"json", "ndjson", "markdown"}:
        typer.echo(f"error: unknown --format '{fmt}' (expected json|ndjson|markdown)", err=True)
        raise typer.Exit(code=LLM_EXIT_USAGE)
    if preset:
        from simdref.filters import ARCH_PRESETS
        if preset not in ARCH_PRESETS:
            known = ", ".join(sorted(ARCH_PRESETS))
            typer.echo(f"error: unknown --preset '{preset}' (known: {known})", err=True)
            raise typer.Exit(code=LLM_EXIT_USAGE)
        preset_isa, _ = _resolve_preset_filters(preset)
        if preset_isa and not isa:
            isa = preset_isa
    if not query_tokens:
        typer.echo("error: query required (or use `simdref llm list` / `simdref llm schema`)", err=True)
        raise typer.Exit(code=LLM_EXIT_USAGE)
    query_str = " ".join(query_tokens)
    ensure_runtime()
    try:
        with open_db() as conn:
            intrinsic = load_intrinsic_from_db(conn, query_str)
            if intrinsic is not None:
                result = _llm_intrinsic_payload(conn, intrinsic)
                kept = _llm_filter_records([result], isa, category)
                payload = {
                    "query": query_str, "mode": "exact",
                    "match_kind": "intrinsic" if kept else None,
                    **({"result": kept[0]} if kept else {"results": []}),
                }
            else:
                instructions = _find_instructions_fast(query_str)
                if instructions:
                    items = [_llm_instruction_payload(item) for item in instructions]
                    items = _llm_filter_records(items, isa, category)
                    payload = {
                        "query": query_str, "mode": "exact",
                        "match_kind": "instruction",
                        "results": items,
                    }
                else:
                    results, intrinsic_map, instruction_map = _search_runtime(conn, query_str, limit=limit)
                    items = [_llm_result_payload(conn, r, intrinsic_map, instruction_map) for r in results]
                    items = _llm_filter_records(items, isa, category)
                    payload = {
                        "query": query_str, "mode": "search",
                        "match_kind": None,
                        "results": items,
                    }
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover - internal error path
        typer.echo(f"internal error: {exc}", err=True)
        raise typer.Exit(code=LLM_EXIT_INTERNAL)
    _emit_llm_payload(payload, fmt_lower)
    raise typer.Exit(code=_llm_exit_code(payload))


@llm_app.command("query")
def llm_query(
    query: list[str] = typer.Argument(..., help="Search query (multiple tokens allowed)."),
    limit: int = typer.Option(8, help="Maximum number of search results in search mode."),
    fmt: str = typer.Option("json", "--format", "-F", help="Output format: json, ndjson, or markdown."),
    isa: list[str] = typer.Option(None, "--isa", help="Filter by ISA family (repeatable)."),
    preset: str = typer.Option(None, "--preset", help="Apply a named preset (default, intel, arm32, arm64, riscv, none, all)."),
) -> None:
    """Resolve a query and emit an LLM-friendly payload.

    Exit codes: 0 match, 2 no-match, 3 ambiguous, 1 usage error, 10 internal.
    """
    _llm_query_impl(query, limit, fmt, isa, None, preset=preset)


@llm_app.command("list")
def llm_list(
    fmt: str = typer.Option("json", "--format", "-F", help="Output format: json or markdown."),
) -> None:
    """Emit the FilterSpec (ISA families, sub-ISAs, categories)."""
    from simdref.filters import build_filter_spec
    ensure_runtime()
    with open_db() as conn:
        spec = build_filter_spec(conn)
    payload = spec.to_json()
    if (fmt or "json").lower() == "markdown":
        lines = ["# simdref filter spec", "", "## ISA families"]
        for fam in payload["default_enabled"]:
            lines.append(f"- **{fam}** (default)")
        for fam in payload["family_order"]:
            if fam not in payload["default_enabled"]:
                lines.append(f"- {fam}")
        lines += ["", "## Categories"]
        for cat in payload["categories"]:
            lines.append(f"- {cat['family']} / {cat['category']} ({cat['count']})")
        typer.echo("\n".join(lines))
        return
    typer.echo(json.dumps(payload, sort_keys=True, indent=2))


@llm_app.command("schema")
def llm_schema() -> None:
    """Emit the JSON Schema for `simdref llm` payloads."""
    typer.echo(json.dumps(_llm_schema_payload(), sort_keys=True, indent=2))


app.add_typer(llm_app, name="llm")


@app.command()
def doctor() -> None:
    """Validate installation and show catalog stats."""
    catalog = ensure_catalog()
    from rich.table import Table
    table = Table(show_header=False, box=None)
    table.add_row("catalog", str(CATALOG_PATH))
    table.add_row("sqlite", f"{SQLITE_PATH} exists={SQLITE_PATH.exists()}")
    table.add_row("man", str(DEFAULT_MAN_DIR))
    table.add_row("intrinsics", str(len(catalog.intrinsics)))
    table.add_row("instructions", str(len(catalog.instructions)))
    for source in catalog.sources:
        table.add_row(
            f"source {source.source}",
            f"version={source.version} url={source.url} fixture={source.used_fixture}",
        )
    console.print(table)


def _export_web_impl(web_dir: Path) -> None:
    catalog = ensure_catalog()
    export_web(catalog, web_dir)
    console.print(f"exported static web app to {web_dir}", style="green")


@app.command("web")
def web_command(web_dir: Path = typer.Option(WEB_DIR, help="Output directory for static assets.")) -> None:
    """Export static web app."""
    _export_web_impl(web_dir)


@app.command("export-web", hidden=True)
def export_web_command(web_dir: Path = typer.Option(WEB_DIR, help="Output directory for static assets.")) -> None:
    """Export static web app."""
    _export_web_impl(web_dir)


@app.command("serve")
def serve_command(
    web_dir: Path = typer.Option(WEB_DIR, help="Directory to serve (usually the export dir)."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    preset: str = typer.Option(None, "--preset", help="Open URL with ?preset=NAME so the web UI applies it on load."),
) -> None:
    """Serve the exported web app with gzip support.

    Prefers pre-compressed ``*.json.gz`` sidecars written by ``simdref web``
    when the client sends ``Accept-Encoding: gzip``; falls back to plain files.
    """
    import http.server
    import os
    import socketserver

    web_dir = Path(web_dir).resolve()
    if not web_dir.is_dir():
        console.print(f"[red]directory not found: {web_dir}[/red]")
        raise typer.Exit(1)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_dir), **kwargs)

        def do_GET(self) -> None:  # noqa: N802
            accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
            url_path = self.path.split("?", 1)[0].split("#", 1)[0]
            rel = url_path.lstrip("/")
            target = (web_dir / rel).resolve()
            # Containment check.
            try:
                target.relative_to(web_dir)
            except ValueError:
                self.send_error(403)
                return
            if target.is_dir():
                target = target / "index.html"
            gz_candidate = Path(str(target) + ".gz")
            if accepts_gzip and target.suffix == ".json" and gz_candidate.is_file():
                try:
                    data = gz_candidate.read_bytes()
                except OSError:
                    super().do_GET()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=60")
                self.end_headers()
                self.wfile.write(data)
                return
            super().do_GET()

    os.chdir(web_dir)

    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    with _Server((host, port), Handler) as srv:
        query_suffix = ""
        if preset:
            from urllib.parse import quote
            query_suffix = f"?preset={quote(preset)}"
        console.print(
            f"serving [cyan]{web_dir}[/cyan] at [cyan]http://{host}:{port}/{query_suffix}[/cyan] (gzip-aware)"
        )
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point — dispatches to subcommand or smart lookup."""
    global SHOW_FP16_ISAS, SHORT_MODE, FULL_MODE
    argv = sys.argv[1:]
    if _is_completion_invocation():
        app()
        return 0
    if "--fp16" in argv:
        SHOW_FP16_ISAS = True
        argv = [arg for arg in argv if arg != "--fp16"]
    if "--short" in argv or "-s" in argv:
        SHORT_MODE = True
        argv = [arg for arg in argv if arg not in ("--short", "-s")]
    if "--full" in argv or "-f" in argv:
        FULL_MODE = True
        argv = [arg for arg in argv if arg not in ("--full", "-f")]
    # Pre-parse top-level --preset NAME / --preset=NAME for bare-query TUI mode.
    # Subcommands (llm, etc.) handle their own --preset via Typer, so only
    # strip it here when it would otherwise reach the smart-lookup dispatch.
    initial_preset: str | None = None
    _cleaned: list[str] = []
    _i = 0
    while _i < len(argv):
        arg = argv[_i]
        if arg == "--preset" and _i + 1 < len(argv):
            initial_preset = argv[_i + 1]
            _i += 2
            continue
        if arg.startswith("--preset="):
            initial_preset = arg.split("=", 1)[1]
            _i += 1
            continue
        _cleaned.append(arg)
        _i += 1
    # Only consume --preset at the top level when the remainder is a bare
    # query or empty; otherwise leave it for the subcommand (e.g. `llm query`).
    subcommand_consumers = {"llm"}
    if _cleaned and _cleaned[0] in subcommand_consumers:
        # Restore; let Typer subcommand parse it.
        pass
    else:
        argv = _cleaned
    # Rewrite `llm <bare-query>` to `llm query <bare-query>` so Typer's
    # subcommand dispatch (list/schema) works without stealing bare queries.
    llm_subcommands = {"query", "list", "schema", "--help", "-h"}
    if argv and argv[0] == "llm" and len(argv) >= 2 and argv[1] not in llm_subcommands and not argv[1].startswith("-"):
        argv = ["llm", "query", *argv[1:]]
    sys.argv = [sys.argv[0], *argv]
    commands = {"update", "search", "show", "man", "doctor", "tui", "web", "export-web", "serve", "llm", "complete", "shell-init", "--help", "-h"}
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        return _smart_lookup(" ".join(argv), preset=initial_preset)
    if not argv:
        ensure_runtime()
        return _run_tui(initial_preset=initial_preset)
    app()
    return 0
