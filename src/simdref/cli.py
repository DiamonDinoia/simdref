"""Command-line interface for simdref.

This module defines the Typer application with all subcommands (``update``,
``search``, ``show``, ``llm``, ``complete``, ``man``, ``doctor``, ``tui``,
``export-web``, ``shell-init``) and the smart bare-word lookup that fires
when no recognised subcommand is given.

Display and formatting logic lives in :mod:`simdref.display`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import typer
from rich.prompt import Prompt

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


app = typer.Typer(help="Search Intel intrinsic and uops.info data from one local catalog.")
SHOW_FP16_ISAS = False

GITHUB_REPO = "DiamonDinoia/simdref"
RELEASE_TAG = "data-latest"


# ---------------------------------------------------------------------------
# Release download helpers
# ---------------------------------------------------------------------------


def _release_asset_url(asset_name: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/releases/download/{RELEASE_TAG}/{asset_name}"


def _download_from_release() -> None:
    """Download pre-built catalog.json and catalog.db from GitHub Release."""
    from simdref.storage import ensure_dir

    ensure_dir(DATA_DIR)

    for asset in ("catalog.json", "catalog.db"):
        url = _release_asset_url(asset)
        dest = DATA_DIR / asset
        console.print(f"downloading {asset}...", style="dim")
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 64):
                        f.write(chunk)
        except httpx.HTTPStatusError as exc:
            console.print(f"failed to download {asset}: {exc.response.status_code}", style="red")
            console.print("the pre-built release may not exist yet — try 'simdref update' to build locally", style="yellow")
            raise typer.Exit(code=1) from exc
    console.print("download complete", style="green")


# ---------------------------------------------------------------------------
# Catalog / runtime helpers
# ---------------------------------------------------------------------------


def _bootstrap_interactive() -> None:
    """Prompt the user to choose how to set up the catalog on first run."""
    console.print("\n[bold]No catalog found.[/bold] How would you like to set up simdref?\n")
    console.print("  [cyan][1][/cyan] Download pre-built data from GitHub Release (recommended, fast)")
    console.print("  [cyan][2][/cyan] Build locally from upstream sources (crawls Intel + uops.info)\n")

    choice = Prompt.ask("Choice", choices=["1", "2"], default="1")

    if choice == "1":
        _download_from_release()
    else:
        catalog = build_catalog(offline=False)
        save_catalog(catalog)
        build_sqlite(catalog)
        write_manpages(catalog, DEFAULT_MAN_DIR)
        export_web(catalog, WEB_DIR)
        console.print(
            f"built catalog with {len(catalog.intrinsics)} intrinsics and {len(catalog.instructions)} instructions",
            style="green",
        )


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
        build_sqlite(load_catalog())


def _catalog_meta(catalog) -> dict:
    return {
        "generated_at": catalog.generated_at,
        "source_versions": [asdict(source) for source in catalog.sources],
    }



def _search_runtime(conn, query: str, limit: int = 20) -> tuple[list[SearchResult], dict[str, object], dict[str, object]]:
    candidate_limit = max(limit * 12, 120)
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
    ensure_runtime()
    family_query = " ".join(query.split()[:-1]).strip() if query.split() and query.split()[-1].isdigit() else query
    family_items = _find_instruction_family_fast(family_query)
    if family_items:
        indexed_family_variant = _select_instruction_variant(None, query, family_items)
        if indexed_family_variant is not None:
            with open_db() as conn:
                render_instruction(None, indexed_family_variant, conn=conn)
            return 0
        if family_query.casefold() == query.casefold():
            render_instruction_variants(query, family_items, show_fp16=SHOW_FP16_ISAS)
            return 0
    with open_db() as conn:
        intrinsic = load_intrinsic_from_db(conn, query)
        if intrinsic is not None:
            render_intrinsic(None, intrinsic, conn=conn)
            return 0
        indexed_variant = _select_instruction_variant(None, query, _find_instructions_fast(" ".join(query.split()[:-1])) if query.split() and query.split()[-1].isdigit() else [])
        if indexed_variant is not None:
            render_instruction(None, indexed_variant, conn=conn)
            return 0
        instructions = _find_instructions_fast(query)
        if instructions:
            exact_form = next((item for item in instructions if item.key.casefold() == query.casefold()), None)
            if exact_form is not None:
                render_instruction(None, exact_form, conn=conn)
            elif len(instructions) == 1:
                render_instruction(None, instructions[0], conn=conn)
            else:
                render_instruction_variants(query, instructions, show_fp16=SHOW_FP16_ISAS)
            return 0
    with open_db() as conn:
        _print_search_results_runtime(conn, query)
    return 0


# ---------------------------------------------------------------------------
# Typer commands
# ---------------------------------------------------------------------------


@app.command()
def update(
    offline: bool = typer.Option(False, help="Use bundled fixtures instead of fetching upstream."),
    from_release: bool = typer.Option(False, "--from-release", help="Download pre-built data from GitHub Release."),
    man_dir: Path = typer.Option(DEFAULT_MAN_DIR, help="Target man root directory."),
) -> None:
    """Rebuild catalog from upstream sources."""
    if from_release:
        _download_from_release()
        return
    catalog = build_catalog(offline=offline)
    save_catalog(catalog)
    build_sqlite(catalog)
    write_manpages(catalog, man_dir)
    export_web(catalog, WEB_DIR)
    console.print(
        f"updated catalog with {len(catalog.intrinsics)} intrinsics and {len(catalog.instructions)} instructions",
        style="green",
    )


@app.command()
def search(query: list[str] = typer.Argument(help="Search query (multiple tokens allowed)."), limit: int = typer.Option(20, help="Maximum number of results.")) -> None:
    """Search intrinsics and instructions."""
    query = " ".join(query)
    ensure_runtime()
    with open_db() as conn:
        _print_search_results_runtime(conn, query, limit=limit)


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
def complete(prefix: str = typer.Argument(""), limit: int = typer.Option(50, help="Maximum number of completion candidates.")) -> None:
    """List completion candidates for a query prefix."""
    ensure_runtime()
    prefix_folded = prefix.casefold()
    with open_db() as conn:
        results, intrinsic_map, instruction_map = _search_runtime(conn, prefix or "_mm", limit=max(limit, 100))
    emitted: list[str] = []
    for result in results:
        if result.kind == "intrinsic":
            item = intrinsic_map.get(result.key)
            if item is not None and not isa_visible(item.isa, show_fp16=SHOW_FP16_ISAS):
                continue
        elif result.kind == "instruction":
            item = instruction_map.get(result.key)
            if item is not None and not isa_visible(item.isa, show_fp16=SHOW_FP16_ISAS):
                continue
        if result.title.casefold().startswith(prefix_folded) or not prefix_folded:
            if result.title not in emitted:
                emitted.append(result.title)
    if prefix_folded:
        for intrinsic in intrinsic_map.values():
            if isa_visible(intrinsic.isa, show_fp16=SHOW_FP16_ISAS) and intrinsic.name.casefold().startswith(prefix_folded) and intrinsic.name not in emitted:
                emitted.append(intrinsic.name)
        for instruction in instruction_map.values():
            if isa_visible(instruction.isa, show_fp16=SHOW_FP16_ISAS) and instruction.key.casefold().startswith(prefix_folded) and instruction.key not in emitted:
                emitted.append(instruction.key)
    for candidate in emitted[:limit]:
        print(candidate)


@app.command("shell-init")
def shell_init(shell: str = typer.Argument("bash")) -> None:
    """Print shell completion setup script."""
    if shell != "bash":
        raise typer.BadParameter("only bash is supported")
    print(
        """
_simdref_complete() {
  local cur
  cur="${COMP_WORDS[COMP_CWORD]}"
  COMPREPLY=( $(simdref complete "$cur") )
}
complete -F _simdref_complete simdref
""".strip()
    )


@app.command()
def show(kind: str, name: str) -> None:
    """Display a specific intrinsic or instruction."""
    ensure_runtime()
    if kind == "intrinsic":
        with open_db() as conn:
            item = load_intrinsic_from_db(conn, name)
            if item is None:
                raise typer.Exit(code=1)
            render_intrinsic(None, item, conn=conn)
        return
    items = _find_instructions_fast(name)
    if not items:
        raise typer.Exit(code=1)
    with open_db() as conn:
        exact_form = next((item for item in items if item.key.casefold() == name.casefold()), None)
        if exact_form is not None:
            render_instruction(None, exact_form, conn=conn)
        elif len(items) == 1:
            render_instruction(None, items[0], conn=conn)
        else:
            render_instruction_variants(name, items, show_fp16=SHOW_FP16_ISAS)


@app.command()
def man(name: str, man_dir: Path = typer.Option(DEFAULT_MAN_DIR, help="Root man directory.")) -> None:
    """Open manpage for intrinsic or instruction."""
    catalog = ensure_catalog()
    intrinsic = find_intrinsic(catalog, name)
    if intrinsic is not None:
        raise typer.Exit(code=open_manpage(name, man_dir))
    instructions = find_instructions(catalog, name)
    if instructions:
        raise typer.Exit(code=open_manpage(instructions[0].mnemonic, man_dir))
    raise typer.Exit(code=1)


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


@app.command()
def tui() -> None:
    """Run the interactive terminal UI."""
    catalog = ensure_catalog()
    raise typer.Exit(code=run_tui(catalog))


@app.command("export-web")
def export_web_command(web_dir: Path = typer.Option(WEB_DIR, help="Output directory for static assets.")) -> None:
    """Export static web app."""
    catalog = ensure_catalog()
    export_web(catalog, web_dir)
    console.print(f"exported static web app to {web_dir}", style="green")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point — dispatches to subcommand or smart lookup."""
    global SHOW_FP16_ISAS
    argv = sys.argv[1:]
    if "--fp16" in argv:
        SHOW_FP16_ISAS = True
        argv = [arg for arg in argv if arg != "--fp16"]
        sys.argv = [sys.argv[0], *argv]
    commands = {"update", "search", "show", "man", "doctor", "tui", "export-web", "llm", "complete", "shell-init", "--help", "-h"}
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        return _smart_lookup(" ".join(argv))
    app()
    return 0
