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
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer.core import TyperGroup

from simdref.display import (
    console,
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
from simdref.tui import run_tui
from simdref.web import export_web


class SimdrefGroup(TyperGroup):
    """Top-level CLI group with usage that reflects bare-query mode."""

    def collect_usage_pieces(self, ctx):  # type: ignore[override]
        return ["[OPTIONS] [QUERY] | COMMAND [ARGS]..."]


app = typer.Typer(
    cls=SimdrefGroup,
    help="Local SIMD reference across Intel intrinsics, instruction data, performance measurements, and SDM-derived descriptions.\n\nRun without arguments to open the TUI. Pass a bare query to search or open matching results directly.",
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
            console.print(f"downloading {asset} from {tag}...", style="dim")
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
                console.print(f"failed to download {asset}: {exc.response.status_code}", style="red")
                raise typer.Exit(code=1) from exc
        else:
            console.print(f"failed to download {asset}: no compatible release asset found", style="red")
            console.print("try 'simdref update --build-local' to build locally", style="yellow")
            raise typer.Exit(code=1)
    console.print("download complete", style="green")


def _build_runtime_locally(*, offline: bool, man_dir: Path, include_sdm: bool = False) -> None:
    """Build catalog, SQLite, manpages, and web bundle locally."""
    interactive_progress = console.is_terminal and os.environ.get("GITHUB_ACTIONS") != "true"

    if not interactive_progress:
        def _status(msg: str) -> None:
            console.print(msg, style="dim")

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
        console.print(
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
        console=console,
    )
    with progress:
        task = progress.add_task("Building local catalog", total=4)

        def _status(msg: str) -> None:
            progress.update(task, description=msg)

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

    console.print(
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
    console.print(
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
    console.print(
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
            console.print("downloaded catalog is usable but SQLite is stale; rebuilding runtime locally from the downloaded catalog", style="yellow")
            _refresh_runtime_from_existing_catalog(man_dir=man_dir)
            return
        console.print("downloaded runtime schema is not current; falling back to bundled fixtures", style="yellow")
    except typer.Exit:
        if CATALOG_PATH.exists():
            console.print("download failed; refreshing runtime from the existing local catalog", style="yellow")
            _refresh_runtime_from_existing_catalog(man_dir=man_dir)
            return
        if not fallback_offline:
            raise
        console.print("falling back to bundled fixtures", style="yellow")

    _build_runtime_locally(offline=True, man_dir=man_dir)


# ---------------------------------------------------------------------------
# Catalog / runtime helpers
# ---------------------------------------------------------------------------


def _bootstrap_interactive() -> None:
    """Bootstrap runtime data with a lightweight default path."""
    console.print("\n[bold]No catalog found.[/bold] Downloading pre-built data if available...\n")
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
        console.print("runtime schema is missing or out of date; rebuilding derived runtime artifacts from the local catalog", style="yellow")
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
    instruction_map = {item.key: item for item in instructions}
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
        isa = "-"
        lat = "-"
        cpi = "-"
        isa_sort = (99, "-")
        if result.kind == "instruction":
            item = instruction_map.get(result.key)
            if item is not None:
                if not isa_visible(item.isa, show_fp16=SHOW_FP16_ISAS):
                    continue
                isa = display_isa(item.isa)
                isa_sort = isa_sort_key(item.isa)
                lat, cpi = variant_perf_summary(item.arch_details)
        elif result.kind == "intrinsic":
            item = intrinsic_map.get(result.key)
            if item is not None:
                if not isa_visible(item.isa, show_fp16=SHOW_FP16_ISAS):
                    continue
                isa = display_isa(item.isa)
                isa_sort = isa_sort_key(item.isa)
                lat, cpi = intrinsic_perf_summary_runtime(conn, item, instruction_map)
        prepared_rows.append((result, isa, lat, cpi, isa_sort))
    prepared_rows.sort(key=lambda row: (row[0].kind != "instruction", row[4], row[0].title.casefold(), row[0].key.casefold()))
    render_search_results([(r, isa, lat, cpi) for r, isa, lat, cpi, _ in prepared_rows])


# ---------------------------------------------------------------------------
# Smart lookup (bare-word query)
# ---------------------------------------------------------------------------


def _smart_lookup(query: str) -> int:
    """Open the TUI pre-filled with the given query."""
    ensure_runtime()
    return run_tui(initial_query=query)


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


@app.command()
def llm(query: list[str] = typer.Argument(help="Search query (multiple tokens allowed)."), limit: int = typer.Option(8, help="Maximum number of search results in search mode.")) -> None:
    """Output structured JSON for LLM consumption."""
    query = " ".join(query)
    ensure_runtime()
    with open_db() as conn:
        intrinsic = load_intrinsic_from_db(conn, query)
        if intrinsic is not None:
            payload = {
                "query": query,
                "mode": "exact",
                "match_kind": "intrinsic",
                "result": _llm_intrinsic_payload(conn, intrinsic),
            }
        else:
            instructions = _find_instructions_fast(query)
            if instructions:
                payload = {
                    "query": query,
                    "mode": "exact",
                    "match_kind": "instruction",
                    "results": [_llm_instruction_payload(item) for item in instructions],
                    }
            else:
                results, intrinsic_map, instruction_map = _search_runtime(conn, query, limit=limit)
                payload = {
                    "query": query,
                    "mode": "search",
                    "match_kind": None,
                    "results": [_llm_result_payload(conn, result, intrinsic_map, instruction_map) for result in results],
                    }
    console.print_json(json.dumps(payload, sort_keys=True))


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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point — dispatches to subcommand or smart lookup."""
    global SHOW_FP16_ISAS, SHORT_MODE, FULL_MODE
    argv = sys.argv[1:]
    if "--fp16" in argv:
        SHOW_FP16_ISAS = True
        argv = [arg for arg in argv if arg != "--fp16"]
    if "--short" in argv or "-s" in argv:
        SHORT_MODE = True
        argv = [arg for arg in argv if arg not in ("--short", "-s")]
    if "--full" in argv or "-f" in argv:
        FULL_MODE = True
        argv = [arg for arg in argv if arg not in ("--full", "-f")]
    sys.argv = [sys.argv[0], *argv]
    commands = {"update", "search", "show", "man", "doctor", "tui", "web", "export-web", "llm", "complete", "shell-init", "--help", "-h"}
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        return _smart_lookup(" ".join(argv))
    if not argv:
        ensure_runtime()
        return run_tui()
    app()
    return 0
