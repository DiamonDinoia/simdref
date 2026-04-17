"""Interactive Textual-based terminal UI for simdref.

Provides fuzzy search across intrinsics and instructions with
collapsible detail sections, keyboard navigation, and Rich rendering.

Uses SQLite FTS for fast search instead of loading the full catalog.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import TYPE_CHECKING

_PROFILE = os.environ.get("SIMDREF_PROFILE") == "1"


def _profile_log(tag: str, elapsed_ms: float, **extras: object) -> None:
    if not _PROFILE:
        return
    extras_str = " ".join(f"{k}={v}" for k, v in extras.items())
    sys.stderr.write(f"[simdref-profile] {tag} {elapsed_ms:.1f}ms {extras_str}\n")

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
try:
    from textual import events, on, work
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
except ImportError:  # pragma: no cover - allows non-TUI test environments
    class _TextualStub:
        def __init__(self, *args, **kwargs):
            pass

    class _EventStub:
        pass

    def on(*args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator

    def work(*args, **kwargs):
        def _decorator(fn):
            return fn
        return _decorator

    class App:  # type: ignore[override]
        pass

    ComposeResult = object
    Message = _TextualStub
    Binding = _TextualStub
    Horizontal = _TextualStub
    VerticalScroll = _TextualStub
    Collapsible = _TextualStub
    Footer = _TextualStub
    Header = _TextualStub
    Input = _TextualStub
    Input.Changed = _EventStub
    Input.Submitted = _EventStub
    Label = _TextualStub
    ListItem = _TextualStub
    ListView = _TextualStub
    ListView.Selected = _EventStub
    ListView.Highlighted = _EventStub
    Static = _TextualStub
    events = _TextualStub()

from simdref.display import (
    _CODE_SECTION_LANG,
    _DESCRIPTION_ORDER,
    _MEASUREMENT_PREFERRED_ORDER,
    DEFAULT_ENABLED_ISAS,
    DEFAULT_SUBS,
    FAMILY_SUB_ORDER,
    ISA_FAMILY_ORDER,
    canonical_url,
    display_isa,
    display_instruction_form,
    display_uarch,
    isa_family,
    isa_to_sub_isa,
    instruction_metadata_rows,
    normalize_isa_token,
    strip_instruction_decorators,
    measurement_rows,
    uarch_sort_key,
)
from simdref.perf import variant_perf_summary
from simdref.queries import linked_instruction_records
from simdref.search import SearchResult
from simdref.storage import (
    SQLITE_PATH,
    load_instruction_from_db,
    load_intrinsic_from_db,
    open_db,
    sqlite_schema_is_current,
)

from simdref.models import InstructionRecord, IntrinsicRecord

_INITIAL_RESULT_BATCH = 50
_RESULT_BATCH_SIZE = 10
_RESULT_PREFETCH_THRESHOLD = 5

_ISA_FAMILIES: list[str] = [name for name, _ in sorted(ISA_FAMILY_ORDER.items(), key=lambda item: item[1])]


def _isa_matches_sub(isa: str, sub_isa: str) -> bool:
    """Return whether a raw ISA token belongs to the given sub-ISA."""
    normalized_isa = normalize_isa_token(isa)
    normalized_sub = normalize_isa_token(sub_isa)
    return normalized_isa == normalized_sub or (
        normalized_isa.startswith(normalized_sub) and len(normalized_isa) > len(normalized_sub)
    )


def _normalize_sub_isa_selection(
    enabled_families: set[str],
    enabled_sub_isas: set[str] | None,
    family_subs: dict[str, list[str]],
) -> set[str] | None:
    """Keep only sub-ISAs that belong to enabled families.

    Returns ``None`` when all visible sub-ISAs are effectively enabled.
    """
    visible_subs: set[str] = set()
    for family in enabled_families:
        visible_subs.update(family_subs.get(family, []))
    if not visible_subs:
        return None
    if enabled_sub_isas is None:
        return None
    normalized = {sub for sub in enabled_sub_isas if sub in visible_subs}
    if not normalized or normalized == visible_subs:
        return None
    return normalized


# ---------------------------------------------------------------------------
# FTS search
# ---------------------------------------------------------------------------


def _name_match_score(name: str, terms: list[str]) -> int:
    """Score how well query terms match a name. Higher = better match.

    Rewards: all terms present in name, contiguous terms, exact substrings.
    """
    name_lower = name.lower().replace("_", " ")
    score = 0
    # Count how many terms appear in the name
    matched = sum(1 for t in terms if t in name_lower)
    score += matched * 10
    # Bonus if ALL terms are in the name
    if matched == len(terms):
        score += 50
    # Bonus for contiguous substring: "256 add ps" in "mm256 add ps"
    query_joined = " ".join(terms)
    if query_joined in name_lower:
        score += 100
    # Bonus for underscore-joined match: "256_add_ps" in name
    query_underscored = "_".join(terms)
    if query_underscored in name.lower():
        score += 100
    # Penalty for longer names (prefer more specific matches)
    score -= len(name) // 10
    return score


def _fts_search(
    conn: sqlite3.Connection,
    query: str,
    enabled_families: set[str],
    enabled_sub_isas: set[str] | None = None,
    offset: int = 0,
    limit: int = 30,
) -> list[SearchResult]:
    """Search using SQLite FTS5 with ISA family and sub-ISA filtering."""
    _t_start = time.perf_counter() if _PROFILE else 0.0
    results: list[SearchResult] = []
    fts_query = query.replace('"', '""').replace("*", "")
    if not fts_query.strip():
        return results
    terms = [t.lower() for t in fts_query.replace("_", " ").strip().split() if t]
    if not terms:
        return results
    term_expr = " ".join(f'"{t}"*' for t in terms)
    # Search name/name_tokens first (precise), fall back to all columns (broad)
    fts_expr = f"{{name name_tokens}} : {term_expr}"
    fts_expr_broad = term_expr

    # Build an ISA LIKE-clause fragment so SQLite can drop non-matching rows
    # before we ever hand them to Python. Sub-ISA tokens (e.g. "SSE2") and
    # family names (e.g. "Arm") are substring-matched against the space-joined
    # isa column. Families without a sub-ISA breakdown (RISC-V "x86", etc.)
    # get their family name added so a plain family-level toggle still matches.
    isa_like_tokens: list[str] = []
    if enabled_families:
        for fam in enabled_families:
            subs = FAMILY_SUB_ORDER.get(fam)
            if not subs:
                isa_like_tokens.append(fam)
    if enabled_sub_isas is not None:
        isa_like_tokens.extend(enabled_sub_isas)
    # Normalise the isa column the same way _isa_matches_sub does: strip
    # hyphens so "AVX-512F" matches a pushed-down "AVX512F" pattern. REPLACE
    # isn't indexed, but it runs only over FTS-matching rows.
    if isa_like_tokens:
        normalized_tokens = [tok.replace("-", "") for tok in isa_like_tokens]
        isa_like_sql = "(" + " OR ".join("REPLACE(isa, '-', '') LIKE ?" for _ in normalized_tokens) + ")"
        isa_like_params = [f"%{tok}%" for tok in normalized_tokens]
    else:
        isa_like_sql = ""
        isa_like_params = []

    def _isa_visible(isa_str: str) -> bool:
        parts = [v.strip() for v in isa_str.replace(",", " ").split() if v.strip()]
        families = {isa_family(v) for v in parts}
        if enabled_families and not families & enabled_families:
            return False
        if enabled_sub_isas is not None:
            for p in parts:
                for sub in enabled_sub_isas:
                    if _isa_matches_sub(p, sub):
                        return True
                if isa_family(p) not in FAMILY_SUB_ORDER:
                    return True
            return False
        return True

    # Collect candidates and re-rank by name match quality
    candidates: list[tuple[int, SearchResult]] = []

    seen: set[str] = set()

    def _target_candidate_count() -> int:
        return offset + limit + max(limit, 20)

    # With the ISA LIKE filter in SQL, fetch_limit can stay modest — the
    # filter drops non-matching rows before they reach Python.
    isa_filter_clause = f" AND {isa_like_sql}" if isa_like_sql else ""

    def _query_intrinsic_rows(expr: str) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        fetch_limit = max((offset + limit) * 2, 60)
        while fetch_limit <= 5000:
            for sql in (
                f"SELECT name, summary, description, isa FROM intrinsics_fts WHERE intrinsics_fts MATCH ?{isa_filter_clause} ORDER BY rank LIMIT ?",
                f"SELECT name, description, isa FROM intrinsics_fts WHERE intrinsics_fts MATCH ?{isa_filter_clause} ORDER BY rank LIMIT ?",
            ):
                try:
                    rows = conn.execute(sql, (expr, *isa_like_params, fetch_limit)).fetchall()
                    break
                except sqlite3.OperationalError:
                    continue
            visible = sum(1 for row in rows if row["name"] not in seen and _isa_visible(row["isa"] or ""))
            if visible >= _target_candidate_count() or len(rows) < fetch_limit:
                return rows
            fetch_limit *= 2
        return rows

    def _query_instruction_rows(expr: str) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        fetch_limit = max((offset + limit) * 2, 60)
        while fetch_limit <= 5000:
            try:
                rows = conn.execute(
                    f"""
                    SELECT instructions_data.db_key, instructions_fts.key, instructions_fts.summary, instructions_fts.isa
                    FROM instructions_fts
                    JOIN instructions_data ON instructions_data.rowid = instructions_fts.rowid
                    WHERE instructions_fts MATCH ?{isa_filter_clause.replace('REPLACE(isa,', 'REPLACE(instructions_fts.isa,')}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (expr, *isa_like_params, fetch_limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return rows
            visible = sum(1 for row in rows if row["db_key"] not in seen and _isa_visible(row["isa"] or ""))
            if visible >= _target_candidate_count() or len(rows) < fetch_limit:
                return rows
            fetch_limit *= 2
        return rows

    # Search name columns first (precise), then broaden to all columns.
    # The name-only query uses {name name_tokens} column filter which
    # requires schema v7+; fall back to broad search on OperationalError.
    search_exprs = [fts_expr, fts_expr_broad]
    for expr in search_exprs:
        rows = _query_intrinsic_rows(expr)
        for row in rows:
            if row["name"] in seen or not _isa_visible(row["isa"] or ""):
                continue
            seen.add(row["name"])
            subtitle = (row["summary"] if "summary" in row.keys() else "") or row["description"] or ""
            result = SearchResult(
                kind="intrinsic",
                key=row["name"],
                title=row["name"],
                subtitle=subtitle,
                score=100,
            )
            candidates.append((_name_match_score(row["name"], terms), result))

        irows = _query_instruction_rows(expr)
        for row in irows:
            if row["db_key"] in seen or not _isa_visible(row["isa"] or ""):
                continue
            seen.add(row["db_key"])
            result = SearchResult(
                kind="instruction",
                key=row["db_key"],
                title=strip_instruction_decorators(row["key"]),
                subtitle=row["summary"] or "",
                score=90,
            )
            candidates.append((_name_match_score(row["key"], terms), result))

    # Sort by match score descending, then by kind (intrinsics first)
    candidates.sort(key=lambda c: (-c[0], 0 if c[1].kind == "intrinsic" else 1))
    results = [r for _, r in candidates[offset:offset + limit]]
    if _PROFILE:
        _profile_log("_fts_search", (time.perf_counter() - _t_start) * 1000, q=repr(query), n=len(results))
    return results


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class _ToggleChip(Static):
    """Base clickable toggle chip for ISA/category filters."""

    DEFAULT_CSS = """
    _ToggleChip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 0 0 1;
    }
    _ToggleChip.enabled {
        background: $accent;
        color: $text;
    }
    _ToggleChip.disabled {
        background: $surface;
        color: $text-muted;
    }
    """

    class Toggled(Message):
        """Posted when a toggle chip is clicked."""

        def __init__(self, toggle: _ToggleChip) -> None:
            super().__init__()
            self.toggle = toggle

    def __init__(self, label: str, enabled: bool = False) -> None:
        super().__init__(label)
        self.label_text = label
        self.enabled = enabled
        self.add_class("enabled" if enabled else "disabled")

    def on_click(self) -> None:
        self.enabled = not self.enabled
        self.remove_class("enabled" if not self.enabled else "disabled")
        self.add_class("enabled" if self.enabled else "disabled")
        self.post_message(self.Toggled(self))


class ToggleAllLabel(Static):
    """Clickable label that toggles all sibling chips on/off."""

    class Clicked(Message):
        def __init__(self, label: ToggleAllLabel) -> None:
            super().__init__()
            self.label = label

    def on_click(self) -> None:
        self.post_message(self.Clicked(self))


class PresetButton(Static):
    """Clickable button that applies a named preset."""

    class Clicked(Message):
        def __init__(self, mode: str) -> None:
            super().__init__()
            self.mode = mode

    def __init__(self, label: str, mode: str, **kwargs) -> None:
        super().__init__(label, **kwargs)
        self.mode = mode

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.mode))


class IsaToggle(_ToggleChip):
    """A clickable ISA family toggle chip."""

    def __init__(self, family: str, enabled: bool = False) -> None:
        super().__init__(family, enabled)
        self.family = family


class SubIsaToggle(_ToggleChip):
    """A clickable sub-ISA extension toggle chip."""

    def __init__(self, isa: str, enabled: bool = True) -> None:
        super().__init__(isa, enabled)
        self.isa = isa


class KindToggle(_ToggleChip):
    """Toggle chip for result kind (intrinsic | instruction)."""

    def __init__(self, kind: str, label: str, enabled: bool = True) -> None:
        super().__init__(label, enabled)
        self.kind = kind


class SearchInput(Input):
    """Search box with result-navigation arrow key behavior."""

    def key_down(self) -> None:
        app = self.app
        if hasattr(app, "_move_result_focus"):
            app._move_result_focus(1)

    def key_up(self) -> None:
        app = self.app
        if hasattr(app, "_move_result_focus"):
            app._move_result_focus(-1)


class ResultItem(ListItem):
    """A search result list item carrying the underlying SearchResult."""

    def __init__(self, result: SearchResult, index: int) -> None:
        self.result = result
        title_color = "cyan" if result.kind == "intrinsic" else "magenta"
        subtitle = (result.subtitle or "").split("\n")[0]
        label = f"[dim]{index:>2}[/] [{title_color} bold]{result.title}[/]  [dim italic]{subtitle}[/]"
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
    #kind-bar {
        height: 1;
        margin: 0 1;
        overflow-x: auto;
    }
    #kind-bar .isa-label {
        width: auto;
        color: $text-muted;
        padding: 0 1 0 0;
        text-style: underline;
    }
    #sub-isa-container {
        height: auto;
        max-height: 10;
        margin: 0 1;
        overflow-y: auto;
    }
    .sub-isa-row {
        height: 1;
    }
    .sub-isa-row .sub-fam-label {
        width: 10;
        color: $text-muted;
        text-style: underline;
    }
    #isa-bar .isa-label {
        width: auto;
        color: $text-muted;
        padding: 0 1 0 0;
        text-style: underline;
    }
    #isa-bar .preset-btn {
        width: auto;
        background: $surface;
        color: $warning;
        padding: 0 1;
        margin: 0 0 0 1;
        text-style: bold;
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
        Binding("c", "copy_detail", "Copy", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+d", "quit", "Quit", show=True, priority=True, key_display="^d"),
        *[Binding(str(n), f"pick({n})", show=False) for n in range(1, 10)],
    ]

    def __init__(self, initial_query: str = "") -> None:
        super().__init__()
        self._initial_query = initial_query
        self._current_results: list[SearchResult] = []
        self._current_query: str = ""
        self._has_more_results = False
        self._all_expanded = False
        self._conn: sqlite3.Connection | None = None
        self._enabled_families: set[str] = set(DEFAULT_ENABLED_ISAS)
        # Build initial sub-ISA set from defaults for enabled families
        initial_subs: set[str] = set()
        for fam in DEFAULT_ENABLED_ISAS:
            initial_subs.update(DEFAULT_SUBS.get(fam, set()))
        self._enabled_sub_isas: set[str] | None = initial_subs if initial_subs else None
        self._current_detail: IntrinsicRecord | InstructionRecord | None = None
        self._batch_toggle = False
        # Map family -> list of sub-ISA names present in the DB
        self._family_subs: dict[str, list[str]] = {}
        self._enabled_kinds: set[str] = {"intrinsic", "instruction"}
        self._enabled_arm_arch: set[str] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield SearchInput(
            placeholder="Search intrinsics and instructions...",
            id="search-input",
            value=self._initial_query,
        )
        with Horizontal(id="isa-bar"):
            yield ToggleAllLabel("ISA:", classes="isa-label", id="isa-toggle-all")
            for family in _ISA_FAMILIES:
                yield IsaToggle(family, enabled=family in self._enabled_families)
            yield PresetButton("Default", "default", classes="preset-btn")
            yield PresetButton("Intel", "intel", classes="preset-btn")
            yield PresetButton("Arm32", "arm32", classes="preset-btn")
            yield PresetButton("Arm64", "arm64", classes="preset-btn")
            yield PresetButton("RISC-V", "riscv", classes="preset-btn")
            yield PresetButton("None", "none", classes="preset-btn")
            yield PresetButton("All", "all", classes="preset-btn")
        yield VerticalScroll(id="sub-isa-container")
        with Horizontal(id="kind-bar"):
            yield ToggleAllLabel("Kind:", classes="isa-label", id="kind-label")
            yield KindToggle("intrinsic", "intrinsics", enabled="intrinsic" in self._enabled_kinds)
            yield KindToggle("instruction", "asm", enabled="instruction" in self._enabled_kinds)
        yield ListView(id="results-list")
        yield VerticalScroll(id="detail-scroll")
        yield Label("", id="status-label")
        yield Footer()

    def on_mount(self) -> None:
        if not sqlite_schema_is_current():
            self._needs_update = True
            self.query_one("#status-label", Label).update(
                f"  Database not found or outdated. Press [bold]y[/] to update, [bold]n[/] to quit."
            )
            return
        self._needs_update = False
        self._conn = open_db()
        self._build_family_subs()
        # Defer until first layout pass so container width is known
        self.call_after_refresh(self._refresh_sub_isa_bar)
        if self._initial_query:
            self._run_initial_query()
        else:
            self.query_one("#search-input", Input).focus()

    def on_resize(self) -> None:
        # Reflow sub-ISA chips only when width actually changes
        container = self.query_one("#sub-isa-container", VerticalScroll)
        new_w = container.size.width or 0
        if new_w and new_w != getattr(self, "_last_sub_width", 0):
            self._last_sub_width = new_w
            self.call_after_refresh(self._refresh_sub_isa_bar)

    def _build_family_subs(self) -> None:
        """Build sub-ISA lists from ISA tokens present in the current DB."""
        assert self._conn is not None
        family_subs: dict[str, set[str]] = {}
        rows = self._conn.execute(
            """
            SELECT isa FROM instructions_data
            UNION ALL
            SELECT isa FROM intrinsics_data
            """
        ).fetchall()
        for row in rows:
            for raw_isa in str(row["isa"] or "").replace(",", " ").split():
                family = isa_family(raw_isa)
                sub_isa = isa_to_sub_isa(raw_isa)
                if family and sub_isa:
                    family_subs.setdefault(family, set()).add(sub_isa)
        for fam, subs in family_subs.items():
            self._family_subs[fam] = sorted(subs, key=lambda sub: FAMILY_SUB_ORDER.get(fam, []).index(sub) if sub in FAMILY_SUB_ORDER.get(fam, []) else len(FAMILY_SUB_ORDER.get(fam, [])))
        self._enabled_sub_isas = _normalize_sub_isa_selection(self._enabled_families, self._enabled_sub_isas, self._family_subs)

    def _refresh_sub_isa_bar(self) -> None:
        """Rebuild the sub-ISA rows — one or more rows per enabled family."""
        container = self.query_one("#sub-isa-container", VerticalScroll)
        container.remove_children()

        max_width = container.size.width or 120
        label_width = 10  # width of .sub-fam-label

        for fam in _ISA_FAMILIES:
            if fam not in self._enabled_families or fam not in self._family_subs:
                continue
            subs = self._family_subs[fam]
            defaults = DEFAULT_SUBS.get(fam, set())

            # Build toggles with their approximate widths
            toggles: list[SubIsaToggle] = []
            for sub in subs:
                if self._enabled_sub_isas is not None:
                    enabled = sub in self._enabled_sub_isas
                else:
                    enabled = sub in defaults if defaults else True
                toggles.append(SubIsaToggle(sub, enabled=enabled))

            # Split into rows that fit within max_width
            row_children: list[ToggleAllLabel | SubIsaToggle] = [
                ToggleAllLabel(f"{fam}:", classes="sub-fam-label"),
            ]
            used = label_width
            for toggle in toggles:
                chip_w = len(toggle.isa) + 3  # padding + margin
                if used + chip_w > max_width and len(row_children) > 1:
                    container.mount(Horizontal(*row_children, classes="sub-isa-row"))
                    # Continuation row: indent with blank label
                    row_children = [Static(" " * label_width, classes="sub-fam-label")]
                    used = label_width
                row_children.append(toggle)
                used += chip_w
            if len(row_children) > 1:
                container.mount(Horizontal(*row_children, classes="sub-isa-row"))

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

    @on(_ToggleChip.Toggled)
    def on_toggle_changed(self, event: _ToggleChip.Toggled) -> None:
        """Re-run search when ISA or sub-ISA filter changes."""
        toggle = event.toggle
        if isinstance(toggle, IsaToggle):
            if toggle.enabled:
                self._enabled_families.add(toggle.family)
                # Add default subs for newly enabled family
                defaults = DEFAULT_SUBS.get(toggle.family, set())
                if defaults and self._enabled_sub_isas is not None:
                    self._enabled_sub_isas.update(defaults)
            else:
                self._enabled_families.discard(toggle.family)
                # Remove subs belonging to disabled family
                if self._enabled_sub_isas is not None:
                    fam_subs = set(self._family_subs.get(toggle.family, []))
                    self._enabled_sub_isas -= fam_subs
            self._enabled_sub_isas = _normalize_sub_isa_selection(self._enabled_families, self._enabled_sub_isas, self._family_subs)
            self._refresh_sub_isa_bar()
        elif isinstance(toggle, SubIsaToggle):
            all_subs = list(self.query(SubIsaToggle))
            enabled = {t.isa for t in all_subs if t.enabled}
            all_shown = {t.isa for t in all_subs}
            self._enabled_sub_isas = enabled if enabled != all_shown else None
            self._enabled_sub_isas = _normalize_sub_isa_selection(self._enabled_families, self._enabled_sub_isas, self._family_subs)
        elif isinstance(toggle, KindToggle):
            if toggle.enabled:
                self._enabled_kinds.add(toggle.kind)
            else:
                self._enabled_kinds.discard(toggle.kind)
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._do_search(query)

    @on(ToggleAllLabel.Clicked)
    def on_toggle_all_clicked(self, event: ToggleAllLabel.Clicked) -> None:
        """Toggle all chips in the same bar/row."""
        label = event.label
        if label.id == "isa-toggle-all":
            toggles = list(self.query(IsaToggle))
            target = not any(t.enabled for t in toggles)
            for t in toggles:
                t.enabled = target
                t.remove_class("enabled" if not target else "disabled")
                t.add_class("enabled" if target else "disabled")
            self._enabled_families = {t.family for t in toggles if t.enabled}
            if target:
                # Add defaults for all families
                subs: set[str] = set()
                for fam in self._enabled_families:
                    subs.update(DEFAULT_SUBS.get(fam, set()))
                self._enabled_sub_isas = subs if subs else None
            else:
                self._enabled_sub_isas = set()
            self._enabled_sub_isas = _normalize_sub_isa_selection(self._enabled_families, self._enabled_sub_isas, self._family_subs)
            self._refresh_sub_isa_bar()
        else:
            # Family-level sub toggle: find sibling SubIsaToggles in same row
            row = label.parent
            if row is None:
                return
            toggles = list(row.query(SubIsaToggle))
            if not toggles:
                return
            target = not any(t.enabled for t in toggles)
            for t in toggles:
                t.enabled = target
                t.remove_class("enabled" if not target else "disabled")
                t.add_class("enabled" if target else "disabled")
            # Rebuild enabled_sub_isas from all visible toggles
            all_subs = list(self.query(SubIsaToggle))
            enabled = {t.isa for t in all_subs if t.enabled}
            self._enabled_sub_isas = enabled if enabled else set()
            self._enabled_sub_isas = _normalize_sub_isa_selection(self._enabled_families, self._enabled_sub_isas, self._family_subs)
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._do_search(query)

    @on(PresetButton.Clicked)
    def on_preset_clicked(self, event: PresetButton.Clicked) -> None:
        """Apply ISA presets — families + subs + arm_arch + kind in one step."""
        from simdref.filters import ARCH_PRESETS

        preset = ARCH_PRESETS.get(event.mode) or ARCH_PRESETS["default"]
        if event.mode == "all":
            self._enabled_families = set(_ISA_FAMILIES)
            self._enabled_sub_isas = None
        else:
            self._enabled_families = {f for f in _ISA_FAMILIES if f in preset.families}
            preset_subs = set(preset.subs)
            subs: set[str] = set()
            for fam in self._enabled_families:
                available = set(self._family_subs.get(fam, []))
                subs.update(available & preset_subs)
            self._enabled_sub_isas = subs if subs else set()
        self._enabled_arm_arch = set(preset.arm_arch) if preset.arm_arch else None
        self._enabled_kinds = set(preset.kind)
        self._enabled_sub_isas = _normalize_sub_isa_selection(self._enabled_families, self._enabled_sub_isas, self._family_subs)
        # Update ISA toggle visuals
        for t in self.query(IsaToggle):
            target = t.family in self._enabled_families
            t.enabled = target
            t.remove_class("enabled" if not target else "disabled")
            t.add_class("enabled" if target else "disabled")
        # Reflect kind toggle state.
        for t in self.query(KindToggle):
            target = t.kind in self._enabled_kinds
            t.enabled = target
            t.remove_class("enabled" if not target else "disabled")
            t.add_class("enabled" if target else "disabled")
        self._refresh_sub_isa_bar()
        query = self.query_one("#search-input", Input).value.strip()
        if query:
            self._do_search(query)

    @work(exclusive=True)
    async def _debounced_search(self, query: str) -> None:
        """Debounce search: cancel previous, wait 150ms, then search."""
        import asyncio
        await asyncio.sleep(0.15)
        self._do_search(query)

    def _move_result_focus(self, delta: int) -> None:
        """Move the highlighted result up or down and refresh detail."""
        if not self._current_results:
            return
        results_list = self.query_one("#results-list", ListView)
        current_index = results_list.index if results_list.index is not None else 0
        current_index = max(0, min(current_index, len(self._current_results) - 1))
        next_index = max(0, min(current_index + delta, len(self._current_results) - 1))
        self._maybe_load_more_results(next_index)
        results_list.index = next_index
        self._show_detail(self._current_results[next_index])

    def _refine_search_from_key(self, event: events.Key) -> bool:
        """Route typing from results/detail back into the search input."""
        search_input = self.query_one("#search-input", Input)
        if event.key == "backspace":
            if not search_input.value:
                return False
            search_input.value = search_input.value[:-1]
            search_input.focus()
            search_input.cursor_position = len(search_input.value)
            self._debounced_search(search_input.value.strip())
            event.prevent_default()
            return True
        if not event.character or event.is_printable is False:
            return False
        if any(getattr(event, name, False) for name in ("ctrl", "alt", "meta")):
            return False
        search_input.value += event.character
        search_input.focus()
        search_input.cursor_position = len(search_input.value)
        self._debounced_search(search_input.value.strip())
        event.prevent_default()
        return True

    def _do_search(self, query: str) -> None:
        results_list = self.query_one("#results-list", ListView)
        results_list.clear()
        self._current_results = []
        self._current_query = query
        self._has_more_results = False
        if not query:
            self.query_one("#status-label", Label).update("")
            return
        assert self._conn is not None
        results = _fts_search(
            self._conn,
            query,
            self._enabled_families,
            self._enabled_sub_isas,
            offset=0,
            limit=_INITIAL_RESULT_BATCH,
        )
        if self._enabled_kinds and len(self._enabled_kinds) < 2:
            results = [r for r in results if r.kind in self._enabled_kinds]
        self._current_results = results
        self._has_more_results = len(results) == _INITIAL_RESULT_BATCH
        for i, result in enumerate(results, 1):
            results_list.append(ResultItem(result, i))
        suffix = " +" if self._has_more_results else ""
        self.query_one("#status-label", Label).update(f"  {len(results)}{suffix} results for '{query}'")
        if results:
            results_list.index = 0
            self._show_detail(results[0])

    def _maybe_load_more_results(self, current_index: int) -> None:
        """Load another page when navigation reaches the end of the loaded list."""
        if not self._has_more_results or current_index < len(self._current_results) - _RESULT_PREFETCH_THRESHOLD:
            return
        assert self._conn is not None
        more = _fts_search(
            self._conn,
            self._current_query,
            self._enabled_families,
            self._enabled_sub_isas,
            offset=len(self._current_results),
            limit=_RESULT_BATCH_SIZE,
        )
        if self._enabled_kinds and len(self._enabled_kinds) < 2:
            more = [r for r in more if r.kind in self._enabled_kinds]
        if not more:
            self._has_more_results = False
            return
        self._current_results.extend(more)
        results_list = self.query_one("#results-list", ListView)
        start = len(self._current_results) - len(more) + 1
        for i, result in enumerate(more, start):
            results_list.append(ResultItem(result, i))
        self._has_more_results = len(more) == _RESULT_BATCH_SIZE
        suffix = " +" if self._has_more_results else ""
        self.query_one("#status-label", Label).update(
            f"  {len(self._current_results)}{suffix} results for '{self._current_query}'"
        )

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
        if item.result in self._current_results:
            self._maybe_load_more_results(self._current_results.index(item.result))
        # Only show detail if the item is in the current results list
        if item.result in self._current_results:
            self._show_detail(item.result)

    def _show_detail(self, result: SearchResult) -> None:
        detail = self.query_one("#detail-scroll", VerticalScroll)
        detail.remove_children()
        assert self._conn is not None

        if result.kind == "intrinsic":
            intrinsic = load_intrinsic_from_db(self._conn, result.key)
            if intrinsic:
                self._current_detail = intrinsic
                self._render_intrinsic_detail(detail, intrinsic)
        else:
            instruction = load_instruction_from_db(self._conn, result.key)
            if instruction:
                self._current_detail = instruction
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
        category_display = intrinsic.category or "-"
        if intrinsic.subcategory:
            category_display = f"{intrinsic.subcategory} / {intrinsic.category}" if intrinsic.category else intrinsic.subcategory
        meta.add_row("category", category_display)
        if intrinsic.description:
            meta.add_row("description", intrinsic.description)
        if intrinsic.notes:
            meta.add_row("notes", "; ".join(intrinsic.notes))
        linked = self._find_linked_instruction(intrinsic)
        if linked:
            for key, value in instruction_metadata_rows(linked):
                if key == "summary":
                    continue
                meta.add_row(key, value)
        container.mount(Static(Panel(meta, title=f"intrinsic: {intrinsic.name}", border_style="cyan")))

        # Operand table (always visible)
        if linked:
            self._mount_operand_table(container, linked)

        # Performance table (always visible)
        if linked:
            self._mount_perf_table(container, linked)

        # Collapsible description sections
        if intrinsic.doc_sections:
            self._mount_description_sections(container, intrinsic.doc_sections)
        if linked and linked.description:
            self._mount_description_sections(container, linked.description)

    def _render_instruction_detail(self, container, item: InstructionRecord) -> None:
        # Metadata panel (always visible)
        meta = Table(show_header=False, box=None)
        meta.add_row("mnemonic", item.mnemonic)
        meta.add_row("form", display_instruction_form(item.form))
        meta.add_row("isa", display_isa(item.isa))
        meta.add_row("summary", item.summary or "-")
        for key, value in instruction_metadata_rows(item):
            if key in {"isa"}:
                continue
            meta.add_row(key, value)
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

    def action_copy_detail(self) -> None:
        """Copy current detail as plain text to system clipboard."""
        record = self._current_detail
        if record is None:
            return
        lines: list[str] = []
        if isinstance(record, IntrinsicRecord):
            lines.append(record.name)
            lines.append(record.signature)
            if record.isa:
                lines.append(f"ISA: {', '.join(record.isa)}")
            if record.category:
                lines.append(f"Category: {record.category}")
            if record.description:
                lines.append("")
                lines.append(record.description)
            if record.instructions:
                lines.append("")
                lines.append(f"Instructions: {', '.join(record.instructions)}")
        elif isinstance(record, InstructionRecord):
            lines.append(record.key)
            if record.summary:
                lines.append(record.summary)
            if record.isa:
                lines.append(f"ISA: {', '.join(record.isa)}")
            if record.form:
                lines.append(f"Form: {record.form}")
        text = "\n".join(lines)
        self._copy_to_clipboard(text)

    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to system clipboard using available tool."""
        for cmd in (
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ):
            if shutil.which(cmd[0]):
                try:
                    subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
                    self.notify("Copied to clipboard", severity="information", timeout=2)
                    return
                except (subprocess.SubprocessError, OSError):
                    continue
        self.notify("No clipboard tool found (install xclip, xsel, or wl-copy)", severity="warning", timeout=3)

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

    def on_key(self, event: events.Key) -> None:
        if getattr(self, "_needs_update", False):
            if event.key == "y":
                event.prevent_default()
                self._run_update()
            elif event.key in ("n", "q"):
                event.prevent_default()
                self.exit(1)
            return

        focused = self.focused
        if isinstance(focused, (ListView, VerticalScroll)):
            if self._refine_search_from_key(event):
                return

    @work(thread=True)
    def _run_update(self) -> None:
        from simdref.ingest import build_catalog
        from simdref.storage import build_sqlite, save_catalog

        def _status(msg: str) -> None:
            self.call_from_thread(
                self.query_one("#status-label", Label).update,
                f"  {msg}",
            )

        _status("Fetching data and parsing...")
        catalog = build_catalog(offline=False, include_sdm=False, status=_status)
        _status(f"Saving catalog ({len(catalog.intrinsics)} intrinsics)...")
        save_catalog(catalog)
        _status("Building search database...")
        build_sqlite(catalog)
        self._needs_update = False
        self._conn = open_db()

        def _finish() -> None:
            self._build_family_subs()
            self.call_after_refresh(self._refresh_sub_isa_bar)
            self.query_one("#status-label", Label).update(
                f"  Updated: {len(catalog.intrinsics)} intrinsics, {len(catalog.instructions)} instructions"
            )
            self.query_one("#search-input", Input).focus()

        self.call_from_thread(_finish)


def run_tui(initial_query: str = "") -> int:
    """Launch the interactive Textual TUI."""
    app = SimdrefApp(initial_query=initial_query)
    app.run()
    return 0
