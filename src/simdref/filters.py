"""Shared filter specification for intrinsic/instruction lookups.

Single source of truth for the ISA-family / sub-ISA / category facets used by
the TUI, web SPA, CLI, and LLM subcommands. The Python constants here are
re-exported through :mod:`simdref.display` for back-compat and serialised to
``filter_spec.json`` for the static web bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sqlite3
from typing import Any, Iterable


ISA_FAMILY_ORDER: dict[str, int] = {
    "x86": 0, "MMX": 1, "SSE": 2, "AVX": 3,
    "AVX-512": 4, "AVX10": 5, "AMX": 6, "APX": 7,
    "Arm": 8, "RISC-V": 9, "SVML": 10, "Other": 11,
}

DEFAULT_ENABLED_ISAS: tuple[str, ...] = ("SSE", "AVX", "AVX-512", "Arm", "RISC-V")

FAMILY_SUB_ORDER: dict[str, list[str]] = {
    "SSE": ["SSE", "SSE2", "SSE3", "SSSE3", "SSE4.1", "SSE4.2"],
    "AVX": ["AVX", "AVX2", "FMA", "F16C", "AVX_VNNI", "AVX_VNNI_INT8",
            "AVX_VNNI_INT16", "AVX_IFMA", "AVX_NE_CONVERT"],
    "AVX-512": [
        "AVX512F", "AVX512VL", "AVX512BW", "AVX512DQ", "AVX512CD",
        "AVX512_VNNI", "AVX512_FP16", "AVX512_BF16",
        "AVX512_VBMI", "AVX512_VBMI2", "AVX512_BITALG",
        "AVX512IFMA52", "AVX512VPOPCNTDQ", "AVX512_VP2INTERSECT",
        "VAES", "VPCLMULQDQ", "GFNI",
    ],
    "AMX": ["AMX-TILE", "AMX-INT8", "AMX-BF16", "AMX-FP16", "AMX-COMPLEX"],
    "Arm": ["NEON", "SVE", "SVE2"],
    "RISC-V": ["V", "ZVE", "ZV"],
}

DEFAULT_SUBS: dict[str, set[str]] = {
    "SSE": {"SSE", "SSE2", "SSE3", "SSSE3", "SSE4.1", "SSE4.2"},
    "AVX": {"AVX", "AVX2", "FMA", "F16C"},
    "AVX-512": {"AVX512F", "AVX512VL", "AVX512BW", "AVX512DQ", "AVX512CD"},
    "AMX": {"AMX-TILE", "AMX-INT8", "AMX-BF16"},
    "Arm": {"NEON", "SVE", "SVE2"},
    "RISC-V": {"V", "ZVE", "ZV"},
}


@dataclass(frozen=True)
class CategorySpec:
    """A category facet derived from the catalog database."""

    family: str
    category: str
    subcategory: str = ""
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "category": self.category,
            "subcategory": self.subcategory,
            "count": self.count,
        }


@dataclass
class FilterSpec:
    """Filterable facets usable by TUI, web, CLI, and LLM consumers."""

    family_order: dict[str, int] = field(default_factory=lambda: dict(ISA_FAMILY_ORDER))
    family_sub_order: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in FAMILY_SUB_ORDER.items()}
    )
    default_enabled: tuple[str, ...] = DEFAULT_ENABLED_ISAS
    default_subs: dict[str, set[str]] = field(
        default_factory=lambda: {k: set(v) for k, v in DEFAULT_SUBS.items()}
    )
    categories: list[CategorySpec] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """JSON-friendly projection used by the web SPA."""
        return {
            "family_order": dict(self.family_order),
            "family_sub_order": {k: list(v) for k, v in self.family_sub_order.items()},
            "default_enabled": list(self.default_enabled),
            "default_subs": {k: sorted(v) for k, v in self.default_subs.items()},
            "categories": [c.to_dict() for c in self.categories],
        }

    # --- in-memory predicate --------------------------------------------------

    def matches(
        self,
        record: Any,
        enabled_families: Iterable[str] | None = None,
        enabled_categories: Iterable[str] | None = None,
    ) -> bool:
        """Return whether *record* passes the given family/category filter.

        Accepts anything with an ``isa`` iterable and optional ``category``.
        """
        families = set(enabled_families) if enabled_families is not None else None
        categories = set(enabled_categories) if enabled_categories is not None else None
        if families is not None:
            from simdref.display import isa_family  # local import avoids cycle
            record_families = {isa_family(v) for v in (getattr(record, "isa", None) or [])}
            if not record_families & families:
                return False
        if categories is not None:
            record_category = getattr(record, "category", None)
            if not record_category:
                record_category = (getattr(record, "metadata", {}) or {}).get("category", "")
            if record_category not in categories:
                return False
        return True

    # --- SQL pushdown helper --------------------------------------------------

    def sql_predicate(
        self,
        table: str,
        enabled_families: Iterable[str] | None = None,
        enabled_categories: Iterable[str] | None = None,
    ) -> tuple[str, list[Any]]:
        """Return a SQL ``WHERE`` fragment and bind values for *table*.

        The current schema has an indexed ``category`` column only on
        ``intrinsics_data``; category filtering on ``instructions_data`` is
        skipped at the SQL level and handled downstream. ISA family filtering
        relies on substring match against the space-joined ``isa`` column.
        """
        clauses: list[str] = []
        binds: list[Any] = []
        if enabled_families:
            family_tokens: set[str] = set()
            for fam in enabled_families:
                family_tokens.update(self.family_sub_order.get(fam, []))
                family_tokens.add(fam)
            if family_tokens:
                clauses.append(
                    "(" + " OR ".join(f"{table}.isa LIKE ?" for _ in family_tokens) + ")"
                )
                binds.extend(f"%{tok}%" for tok in family_tokens)
        if enabled_categories and table == "intrinsics_data":
            placeholders = ",".join("?" for _ in enabled_categories)
            clauses.append(f"{table}.category IN ({placeholders})")
            binds.extend(enabled_categories)
        return (" AND ".join(clauses), binds)


# ---------------------------------------------------------------------------
# DB-backed category aggregation
# ---------------------------------------------------------------------------


def load_categories_from_db(conn: sqlite3.Connection) -> list[CategorySpec]:
    """Derive the category facet list from ``intrinsics_data``.

    Runs at build time so categories are data-driven rather than hardcoded.
    Instruction-side categories are reached through ``metadata->>'category'``
    on the payload and are skipped here (schema lacks an indexed column).
    """
    from simdref.display import isa_family  # local import avoids cycle
    rows = conn.execute(
        """
        SELECT category, subcategory, isa, COUNT(*) AS n
        FROM intrinsics_data
        WHERE category != ''
        GROUP BY category, subcategory, isa
        """
    ).fetchall()
    aggregate: dict[tuple[str, str, str], int] = {}
    for row in rows:
        isa_str = row["isa"] if hasattr(row, "keys") else row[2]
        category = row["category"] if hasattr(row, "keys") else row[0]
        subcategory = row["subcategory"] if hasattr(row, "keys") else row[1]
        count = row["n"] if hasattr(row, "keys") else row[3]
        tokens = [t for t in str(isa_str or "").split() if t]
        families = {isa_family(t) for t in tokens} or {"Other"}
        for family in families:
            key = (family, category, subcategory)
            aggregate[key] = aggregate.get(key, 0) + int(count)
    specs = [
        CategorySpec(family=fam, category=cat, subcategory=sub, count=n)
        for (fam, cat, sub), n in sorted(aggregate.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
    ]
    return specs


def build_filter_spec(conn: sqlite3.Connection | None = None) -> FilterSpec:
    """Build a :class:`FilterSpec`, deriving categories from *conn* if given."""
    spec = FilterSpec()
    if conn is not None:
        try:
            spec.categories = load_categories_from_db(conn)
        except sqlite3.Error:
            spec.categories = []
    return spec
