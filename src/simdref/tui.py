"""Interactive Textual-based terminal UI for simdref.

Provides fuzzy search across intrinsics and instructions with
collapsible detail sections, keyboard navigation, and Rich rendering.

Uses SQLite FTS for fast search instead of loading the full catalog.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from textual import on, work
from textual.app import App, ComposeResult
from textual.message import Message
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Collapsible,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from simdref.display import (
    _CODE_SECTION_LANG,
    _DESCRIPTION_ORDER,
    _MEASUREMENT_PREFERRED_ORDER,
    canonical_url,
    display_isa,
    display_instruction_form,
    display_uarch,
    measurement_rows,
    uarch_sort_key,
)
from simdref.perf import variant_perf_summary
from simdref.queries import linked_instruction_records
from simdref.search import SearchResult
from simdref.storage import (
    load_instruction_from_db,
    load_intrinsic_from_db,
    open_db,
)

if TYPE_CHECKING:
    from simdref.models import InstructionRecord, IntrinsicRecord

# ---------------------------------------------------------------------------
# ISA family mapping (mirrors web UI)
# ---------------------------------------------------------------------------

_ISA_FAMILIES: list[str] = [
    "x86", "MMX", "SSE", "AVX", "AVX2", "AVX-512", "AVX10", "AMX", "APX", "Other",
]

_DEFAULT_ENABLED: set[str] = {"SSE", "AVX", "AVX2", "AVX-512"}


def _isa_family(isa: str) -> str:
    """Map a single ISA string to its family."""
    d = isa.upper().replace(" ", "")
    if not d or d == "-":
        return "Other"
    if d.startswith("APX"):
        return "APX"
    if d.startswith("AMX"):
        return "AMX"
    if d.startswith("AVX10"):
        return "AVX10"
    if d.startswith("AVX512"):
        return "AVX-512"
    if d in ("AVX2", "AVX2GATHER"):
        return "AVX2"
    if d in ("AVX", "FMA", "FMA4", "F16C", "XOP"):
        return "AVX"
    if d.startswith("SSE") or d.startswith("SSSE"):
        return "SSE"
    if d.startswith("MMX") or d in ("3DNOW", "PENTIUMMMX"):
        return "MMX"
    if d in ("I86", "I186", "I386", "I486", "I586", "X87", "CMOV", "ADX", "AES", "PCLMULQDQ", "CRC32") or d.startswith("BMI"):
        return "x86"
    return "Other"


# ---------------------------------------------------------------------------
# FTS search
# ---------------------------------------------------------------------------


def _fts_search(
    conn: sqlite3.Connection,
    query: str,
    enabled_families: set[str],
    limit: int = 30,
) -> list[SearchResult]:
    """Search using SQLite FTS5 with ISA family filtering."""
    results: list[SearchResult] = []
    fts_query = query.replace('"', '""').replace("*", "")
    if not fts_query.strip():
        return results
    terms = [t for t in fts_query.replace("_", " ").strip().split() if t]
    if not terms:
        return results
    fts_expr = " ".join(f'"{t}"*' for t in terms)

    # Fetch more than limit to account for ISA filtering
    fetch_limit = limit * 3

    for row in conn.execute(
        "SELECT name, description, isa FROM intrinsics_fts WHERE intrinsics_fts MATCH ? ORDER BY rank LIMIT ?",
        (fts_expr, fetch_limit),
    ).fetchall():
        isa_str = row["isa"] or ""
        families = {_isa_family(v.strip()) for v in isa_str.split(",") if v.strip()}
        if enabled_families and not families & enabled_families:
            continue
        results.append(SearchResult(
            kind="intrinsic",
            key=row["name"],
            title=row["name"],
            subtitle=row["description"] or "",
            score=100,
        ))
        if len(results) >= limit:
            return results

    remaining = limit - len(results)
    if remaining > 0:
        for row in conn.execute(
            "SELECT key, summary, isa FROM instructions_fts WHERE instructions_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_expr, fetch_limit),
        ).fetchall():
            isa_str = row["isa"] or ""
            families = {_isa_family(v.strip()) for v in isa_str.split(",") if v.strip()}
            if enabled_families and not families & enabled_families:
                continue
            results.append(SearchResult(
                kind="instruction",
                key=row["key"],
                title=row["key"],
                subtitle=row["summary"] or "",
                score=90,
            ))
            if len(results) >= limit:
                break

    return results


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class IsaToggle(Static):
    """A clickable ISA family toggle chip."""

    DEFAULT_CSS = """
    IsaToggle {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 0 0 1;
    }
    IsaToggle.enabled {
        background: $accent;
        color: $text;
    }
    IsaToggle.disabled {
        background: $surface;
        color: $text-muted;
    }
    """

    def __init__(self, family: str, enabled: bool = False) -> None:
        super().__init__(family)
        self.family = family
        self.enabled = enabled
        self.add_class("enabled" if enabled else "disabled")

    def on_click(self) -> None:
        self.enabled = not self.enabled
        self.remove_class("enabled" if not self.enabled else "disabled")
        self.add_class("enabled" if self.enabled else "disabled")
        self.post_message(self.Toggled(self))

    class Toggled(Message):
        """Posted when an ISA toggle is clicked."""

        def __init__(self, toggle: IsaToggle) -> None:
            super().__init__()
            self.toggle = toggle


class ResultItem(ListItem):
    """A search result list item carrying the underlying SearchResult."""

    def __init__(self, result: SearchResult, index: int) -> None:
        self.result = result
        subtitle = result.subtitle[:60] if result.subtitle else ""
        label = f"[cyan]{index:>2}[/] [{result.kind}] {result.title}  [dim]{subtitle}[/]"
        super().__init__(Static(label, markup=True))


class SimdrefApp(App):
    """Interactive SIMD reference browser."""

    TITLE = "simdref"

    CSS = """
    #search-input {
        dock: top;
        margin: 0 1;
    }
    #isa-bar {
        height: 1;
        margin: 0 1;
        overflow-x: auto;
    }
    #results-list {
        height: auto;
        max-height: 20%;
        border: solid $accent;
        margin: 0 1;
        overflow-y: scroll;
    }
    #detail-scroll {
        height: 1fr;
        margin: 0 1;
        border: solid $primary;
    }
    #status-label {
        dock: bottom;
        height: 1;
        margin: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("f", "toggle_all", "Expand/Collapse All", show=True),
        Binding("1-9", "pick(0)", "Pick result", show=True, key_display="1-9"),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+d", "quit", "Quit", show=True, priority=True, key_display="^d"),
        *[Binding(str(n), f"pick({n})", show=False) for n in range(1, 10)],
    ]

    def __init__(self, initial_query: str = "") -> None:
        super().__init__()
        self._initial_query = initial_query
        self._current_results: list[SearchResult] = []
        self._all_expanded = False
        self._conn: sqlite3.Connection | None = None
        self._enabled_families: set[str] = set(_DEFAULT_ENABLED)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Input(
            placeholder="Search intrinsics and instructions...",
            id="search-input",
            value=self._initial_query,
        )
        with Horizontal(id="isa-bar"):
            for family in _ISA_FAMILIES:
                yield IsaToggle(family, enabled=family in self._enabled_families)
        yield ListView(id="results-list")
        yield VerticalScroll(id="detail-scroll")
        yield Label("", id="status-label")
        yield Footer()

    def on_mount(self) -> None:
        self._conn = open_db()
        if self._initial_query:
            self._run_initial_query()
        else:
            self.query_one("#search-input", Input).focus()

    def on_unmount(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @work
    async def _run_initial_query(self) -> None:
        """Populate results from initial query and open the first match."""
        self._do_search(self._initial_query)
        results_list = self.query_one("#results-list", ListView)
        if results_list.children:
            results_list.index = 0
            first = results_list.children[0]
            if isinstance(first, ResultItem):
                self._show_detail(first.result)
                self.query_one("#detail-scroll").focus()

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        self._debounced_search(event.value.strip())

    @on(Input.Submitted, "#search-input")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        """Enter in search box: expand first result, collapse list, move focus."""
        if self._current_results:
            self._show_detail(self._current_results[0])
            results_list = self.query_one("#results-list", ListView)
            results_list.index = 0
            self.query_one("#detail-scroll").focus()

    @on(IsaToggle.Toggled)
    def on_isa_toggled(self, event: IsaToggle.Toggled) -> None:
        """Re-run search when ISA filter changes."""
        toggle = event.toggle
        if toggle.enabled:
            self._enabled_families.add(toggle.family)
        else:
            self._enabled_families.discard(toggle.family)
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._do_search(query)

    @work(exclusive=True)
    async def _debounced_search(self, query: str) -> None:
        """Debounce search: cancel previous, wait 150ms, then search."""
        import asyncio
        await asyncio.sleep(0.15)
        self._do_search(query)

    def _do_search(self, query: str) -> None:
        results_list = self.query_one("#results-list", ListView)
        results_list.clear()
        self._current_results = []
        if not query:
            self.query_one("#status-label", Label).update("")
            return
        assert self._conn is not None
        results = _fts_search(self._conn, query, self._enabled_families, limit=30)
        self._current_results = results
        for i, result in enumerate(results, 1):
            results_list.append(ResultItem(result, i))
        self.query_one("#status-label", Label).update(
            f"  {len(results)} results for '{query}'"
        )
        if results:
            results_list.index = 0
            self._show_detail(results[0])

    @on(ListView.Selected, "#results-list")
    def on_result_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, ResultItem):
            return
        self._show_detail(item.result)

    @on(ListView.Highlighted, "#results-list")
    def on_result_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if not isinstance(item, ResultItem):
            return
        self._show_detail(item.result)

    def _show_detail(self, result: SearchResult) -> None:
        detail = self.query_one("#detail-scroll", VerticalScroll)
        detail.remove_children()
        assert self._conn is not None

        if result.kind == "intrinsic":
            intrinsic = load_intrinsic_from_db(self._conn, result.key)
            if intrinsic:
                self._render_intrinsic_detail(detail, intrinsic)
        else:
            instruction = load_instruction_from_db(self._conn, result.key)
            if instruction:
                self._render_instruction_detail(detail, instruction)

    # ------------------------------------------------------------------
    # Record lookups
    # ------------------------------------------------------------------

    def _find_linked_instruction(self, intrinsic: IntrinsicRecord) -> InstructionRecord | None:
        assert self._conn is not None
        linked = linked_instruction_records(None, intrinsic, conn=self._conn)
        # Prefer one with description data
        for item in linked:
            if item.description:
                return item
        return linked[0] if linked else None

    # ------------------------------------------------------------------
    # Detail rendering
    # ------------------------------------------------------------------

    def _render_intrinsic_detail(self, container, intrinsic: IntrinsicRecord) -> None:
        # Metadata panel (always visible)
        meta = Table(show_header=False, box=None)
        meta.add_row("signature", intrinsic.signature or "-")
        meta.add_row("header", intrinsic.header or "-")
        meta.add_row("isa", ", ".join(intrinsic.isa) or "-")
        meta.add_row("category", intrinsic.category or "-")
        if intrinsic.notes:
            meta.add_row("notes", "; ".join(intrinsic.notes))
        linked = self._find_linked_instruction(intrinsic)
        if linked:
            if linked.summary:
                meta.add_row("summary", linked.summary)
            url = linked.metadata.get("url", "")
            if url:
                meta.add_row("url", canonical_url(url))
            url_ref = linked.metadata.get("url-ref", "")
            if url_ref:
                meta.add_row("reference", canonical_url(url_ref))
        container.mount(Static(Panel(meta, title=f"intrinsic: {intrinsic.name}", border_style="cyan")))

        # Operand table (always visible)
        if linked:
            self._mount_operand_table(container, linked)

        # Performance table (always visible)
        if linked:
            self._mount_perf_table(container, linked)

        # Collapsible description sections
        if linked and linked.description:
            self._mount_description_sections(container, linked.description)

    def _render_instruction_detail(self, container, item: InstructionRecord) -> None:
        # Metadata panel (always visible)
        meta = Table(show_header=False, box=None)
        meta.add_row("mnemonic", item.mnemonic)
        meta.add_row("form", display_instruction_form(item.form))
        meta.add_row("isa", display_isa(item.isa))
        meta.add_row("summary", item.summary or "-")
        url = item.metadata.get("url", "")
        if url:
            meta.add_row("url", canonical_url(url))
        url_ref = item.metadata.get("url-ref", "")
        if url_ref:
            meta.add_row("reference", canonical_url(url_ref))
        if item.metadata.get("category"):
            meta.add_row("category", item.metadata["category"])
        if item.metadata.get("cpl"):
            meta.add_row("cpl", item.metadata["cpl"])
        container.mount(Static(Panel(meta, title=f"instruction: {display_instruction_form(item.form) or item.mnemonic}", border_style="magenta")))

        # Operand table (always visible)
        self._mount_operand_table(container, item)

        # Performance table (always visible)
        self._mount_perf_table(container, item)

        # Collapsible description sections
        if item.description:
            self._mount_description_sections(container, item.description)

    def _mount_operand_table(self, container, item: InstructionRecord) -> None:
        if not item.operand_details:
            return
        table = Table(header_style="bold blue")
        table.add_column("idx", width=4)
        table.add_column("rw", width=4)
        table.add_column("type", width=8)
        table.add_column("width", width=6)
        table.add_column("xtype", width=8)
        table.add_column("name", width=10)
        for operand in item.operand_details:
            rw = "".join(flag for flag in ("r", "w") if operand.get(flag) == "1")
            table.add_row(
                operand.get("idx", "-"),
                rw or "-",
                operand.get("type", "-"),
                operand.get("width", "-"),
                operand.get("xtype", "-"),
                operand.get("name", "-"),
            )
        container.mount(Static(Panel(table, title="operands", border_style="blue")))

    def _mount_perf_table(self, container, item: InstructionRecord) -> None:
        rows = measurement_rows(item)
        if not rows:
            lat, cpi = variant_perf_summary(item.arch_details)
            if lat != "-" or cpi != "-":
                container.mount(Static(f"  [green]latency:[/] {lat}  [green]CPI:[/] {cpi}", markup=True))
            return
        preferred = _MEASUREMENT_PREFERRED_ORDER
        keys = [k for k in preferred if any(k in row for row in rows)]
        columns = keys
        if not columns:
            return
        table = Table(header_style="bold green", expand=True)
        for col in columns:
            label = {"uarch": "microarch", "latency": "LAT", "TP_loop": "CPI", "TP_unrolled": "CPI unroll", "TP_ports": "CPI ports", "TP": "CPI"}.get(col, col)
            table.add_column(label, no_wrap=(col in ("uarch", "ports")))
        for row in sorted(rows, key=lambda r: uarch_sort_key(r.get("uarch", ""))):
            cells = []
            for col in columns:
                val = row.get(col, "-")
                cells.append(display_uarch(str(val)) if col == "uarch" else str(val))
            table.add_row(*cells)
        container.mount(Static(Panel(table, title="measurements", border_style="green")))

    def _mount_description_sections(
        self, container, description: dict[str, str]
    ) -> None:
        shown: set[str] = set()
        for key in _DESCRIPTION_ORDER:
            if key in description:
                self._mount_one_section(container, key, description[key])
                shown.add(key)
        for key, value in description.items():
            if key not in shown:
                self._mount_one_section(container, key, value)

    def _mount_one_section(self, container, title: str, body: str) -> None:
        lang = _CODE_SECTION_LANG.get(title)
        if lang:
            content = Static(Syntax(body, lang, theme="monokai", word_wrap=True))
        else:
            content = Static(body)
        container.mount(
            Collapsible(content, title=title, collapsed=not self._all_expanded)
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_pick(self, index: int) -> None:
        """Select result by number and show its detail."""
        if index < 1 or index > len(self._current_results):
            return
        result = self._current_results[index - 1]
        results_list = self.query_one("#results-list", ListView)
        results_list.index = index - 1
        self._show_detail(result)
        self.query_one("#detail-scroll").focus()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_back(self) -> None:
        detail = self.query_one("#detail-scroll", VerticalScroll)
        if detail.children:
            detail.remove_children()
            self.query_one("#results-list", ListView).focus()
        else:
            self.query_one("#search-input", Input).focus()

    def action_toggle_all(self) -> None:
        self._all_expanded = not self._all_expanded
        for section in self.query(Collapsible):
            section.collapsed = not self._all_expanded


def run_tui(initial_query: str = "") -> int:
    """Launch the interactive Textual TUI."""
    app = SimdrefApp(initial_query=initial_query)
    app.run()
    return 0
