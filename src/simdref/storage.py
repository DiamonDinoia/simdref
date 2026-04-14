"""JSON and SQLite persistence for the simdref catalog.

Provides dual-format storage: a JSON file for portability and complete
serialisation, and a SQLite database with FTS5 virtual tables for fast
full-text search with BM25 ranking.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

import msgpack

from simdref.models import Catalog, InstructionRecord, IntrinsicRecord, SourceVersion


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]


def _default_data_dir() -> Path:
    """Return the platform-appropriate data directory for simdref.

    Uses repo-relative paths for editable/dev installs, and a
    platform-appropriate user data directory for wheel installs.
    """
    # Dev install: pyproject.toml next to src/simdref/
    if (REPO_ROOT / "pyproject.toml").exists() and (REPO_ROOT / "src" / "simdref").is_dir():
        return REPO_ROOT / "data" / "derived"

    # Installed: use platform-appropriate data dir
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "simdref"


DATA_DIR = _default_data_dir()
_is_dev_install = DATA_DIR == REPO_ROOT / "data" / "derived"

if _is_dev_install:
    WEB_DIR = REPO_ROOT / "web"
    DEFAULT_MAN_DIR = REPO_ROOT / "share" / "man"
else:
    WEB_DIR = DATA_DIR / "web"
    DEFAULT_MAN_DIR = DATA_DIR / "man"

CATALOG_PATH = DATA_DIR / "catalog.msgpack"
SQLITE_PATH = DATA_DIR / "catalog.db"
SQLITE_SCHEMA_VERSION = "8"
FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    payload = msgpack.unpackb(path.read_bytes(), raw=False)
    return Catalog.from_dict(payload)


def save_catalog(catalog: Catalog, path: Path = CATALOG_PATH) -> None:
    ensure_dir(path.parent)
    path.write_bytes(msgpack.packb(catalog.to_dict(), use_bin_type=True))


def open_db(path: Path = SQLITE_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def sqlite_schema_is_current(path: Path = SQLITE_PATH) -> bool:
    if not path.exists():
        return False
    conn = sqlite3.connect(path)
    try:
        meta = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        if meta is None:
            return False
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None or row[0] != SQLITE_SCHEMA_VERSION:
            return False
        expected_columns = {"id", "name", "signature", "description", "header", "isa", "category", "subcategory", "payload"}
        actual_columns = {item[1] for item in conn.execute("PRAGMA table_info(intrinsics_data)").fetchall()}
        if expected_columns != actual_columns:
            return False
        expected_instruction_columns = {"key", "mnemonic", "form", "summary", "isa", "payload"}
        actual_instruction_columns = {item[1] for item in conn.execute("PRAGMA table_info(instructions_data)").fetchall()}
        return expected_instruction_columns == actual_instruction_columns
    except sqlite3.Error:
        return False
    finally:
        conn.close()


_ALPHA_NUM_SPLIT = re.compile(r"[a-zA-Z]+|[0-9]+")


def _tokenize_name(name: str) -> str:
    """Split alpha/numeric boundaries for better FTS matching.

    _mm256_add_epi32 → mm 256 add epi 32
    VADDPS (YMM, YMM, YMM) → vaddps ymm ymm ymm
    """
    return " ".join(_ALPHA_NUM_SPLIT.findall(name)).lower()


def build_sqlite(catalog: Catalog, path: Path = SQLITE_PATH) -> None:
    ensure_dir(path.parent)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE sources (
            source TEXT PRIMARY KEY,
            payload BLOB NOT NULL
        );
        CREATE TABLE intrinsics_data (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE,
            signature TEXT NOT NULL,
            description TEXT NOT NULL,
            header TEXT NOT NULL,
            isa TEXT NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL DEFAULT '',
            payload BLOB NOT NULL
        );
        CREATE INDEX idx_intrinsic_name ON intrinsics_data (name);
        CREATE TABLE instructions_data (
            key TEXT PRIMARY KEY COLLATE NOCASE,
            mnemonic TEXT NOT NULL COLLATE NOCASE,
            form TEXT NOT NULL,
            summary TEXT NOT NULL,
            isa TEXT NOT NULL,
            payload BLOB NOT NULL
        );
        CREATE INDEX idx_instruction_mnemonic ON instructions_data (mnemonic);
        CREATE VIRTUAL TABLE intrinsics_fts USING fts5(name, signature, description, header, isa, category, instructions, notes, aliases, summary, name_tokens);
        CREATE VIRTUAL TABLE instructions_fts USING fts5(key, mnemonic, form, summary, isa, linked_intrinsics, aliases, key_tokens);
        """
    )
    cur.execute("INSERT INTO meta VALUES (?, ?)", ("schema_version", SQLITE_SCHEMA_VERSION))
    cur.execute("INSERT INTO meta VALUES (?, ?)", ("generated_at", catalog.generated_at))

    # Sources
    source_rows = [(s.source, msgpack.packb(asdict(s), use_bin_type=True)) for s in catalog.sources]
    cur.executemany("INSERT INTO sources VALUES (?, ?)", source_rows)

    # Build a mnemonic -> summary lookup from instructions for fast access
    _instr_summary: dict[str, str] = {}
    for irec in catalog.instructions:
        if irec.mnemonic and irec.summary and irec.mnemonic not in _instr_summary:
            _instr_summary[irec.mnemonic] = irec.summary

    # Intrinsics data + FTS
    intrinsics_data_rows = []
    intrinsics_fts_rows = []
    for record in catalog.intrinsics:
        payload = msgpack.packb(asdict(record), use_bin_type=True)
        intrinsics_data_rows.append((
            record.name,
            record.signature,
            record.description,
            record.header,
            " ".join(record.isa),
            record.category,
            record.subcategory,
            payload,
        ))
        # Resolve instruction summary from first linked instruction mnemonic
        instr_summary = ""
        if record.instructions:
            mnemonic = record.instructions[0].split("(")[0].split()[0].strip()
            instr_summary = _instr_summary.get(mnemonic, "")
        if not instr_summary and record.description:
            instr_summary = record.description.split(".")[0] + "."
        intrinsics_fts_rows.append((
            record.name,
            record.signature,
            record.description,
            record.header,
            " ".join(record.isa),
            record.category,
            " ".join(record.instructions),
            " ".join(record.notes),
            " ".join(record.aliases),
            instr_summary,
            _tokenize_name(record.name),
        ))
    cur.executemany(
        "INSERT INTO intrinsics_data (name, signature, description, header, isa, category, subcategory, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        intrinsics_data_rows,
    )
    cur.executemany(
        "INSERT INTO intrinsics_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        intrinsics_fts_rows,
    )

    # Instructions data + FTS
    instructions_data_rows = []
    instructions_fts_rows = []
    for record in catalog.instructions:
        payload = msgpack.packb(asdict(record), use_bin_type=True)
        instructions_data_rows.append((
            record.key,
            record.mnemonic,
            record.form,
            record.summary,
            " ".join(record.isa),
            payload,
        ))
        instructions_fts_rows.append((
            record.key,
            record.mnemonic,
            record.form,
            record.summary,
            " ".join(record.isa),
            " ".join(record.linked_intrinsics),
            " ".join(record.aliases),
            _tokenize_name(record.key),
        ))
    cur.executemany(
        "INSERT INTO instructions_data VALUES (?, ?, ?, ?, ?, ?)",
        instructions_data_rows,
    )
    cur.executemany(
        "INSERT INTO instructions_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        instructions_fts_rows,
    )

    conn.commit()
    conn.close()


def load_sources_from_db(conn: sqlite3.Connection) -> list[SourceVersion]:
    rows = conn.execute("SELECT payload FROM sources ORDER BY source").fetchall()
    return [SourceVersion(**msgpack.unpackb(row["payload"], raw=False)) for row in rows]


def generated_at_from_db(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = 'generated_at'").fetchone()
    return row["value"] if row else ""


def load_intrinsic_from_db(conn: sqlite3.Connection, name: str) -> IntrinsicRecord | None:
    row = conn.execute(
        "SELECT payload FROM intrinsics_data WHERE name = ? ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    if not row:
        return None
    return IntrinsicRecord(**msgpack.unpackb(row["payload"], raw=False))


def load_instruction_from_db(conn: sqlite3.Connection, key: str) -> InstructionRecord | None:
    row = conn.execute("SELECT payload FROM instructions_data WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return InstructionRecord(**msgpack.unpackb(row["payload"], raw=False))


def load_instructions_by_mnemonic_from_db(conn: sqlite3.Connection, mnemonic: str) -> list[InstructionRecord]:
    rows = conn.execute("SELECT payload FROM instructions_data WHERE mnemonic = ? ORDER BY key", (mnemonic,)).fetchall()
    return [InstructionRecord(**msgpack.unpackb(row["payload"], raw=False)) for row in rows]


def load_instructions_by_mnemonic_prefix_from_db(conn: sqlite3.Connection, prefix: str, limit: int = 400) -> list[InstructionRecord]:
    rows = conn.execute(
        """
        SELECT payload
        FROM instructions_data
        WHERE mnemonic LIKE ? || '%'
        ORDER BY mnemonic, key
        LIMIT ?
        """,
        (prefix, limit),
    ).fetchall()
    return [InstructionRecord(**msgpack.unpackb(row["payload"], raw=False)) for row in rows]


def _fts_match_query(query: str) -> str:
    tokens = [token.casefold() for token in FTS_TOKEN_RE.findall(query)]
    return " AND ".join(f'"{token}"*' for token in tokens if token)


def search_intrinsic_candidates_from_db(conn: sqlite3.Connection, query: str, limit: int = 200) -> list[IntrinsicRecord]:
    match_query = _fts_match_query(query)
    if not match_query:
        return []
    rows = conn.execute(
        """
        SELECT intrinsics_data.payload
        FROM intrinsics_fts
        JOIN intrinsics_data ON intrinsics_data.id = intrinsics_fts.rowid
        WHERE intrinsics_fts MATCH ?
        ORDER BY bm25(intrinsics_fts), length(intrinsics_data.name), intrinsics_data.name
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    return [IntrinsicRecord(**msgpack.unpackb(row["payload"], raw=False)) for row in rows]


def search_instruction_candidates_from_db(conn: sqlite3.Connection, query: str, limit: int = 200) -> list[InstructionRecord]:
    match_query = _fts_match_query(query)
    if not match_query:
        return []
    rows = conn.execute(
        """
        SELECT instructions_data.payload
        FROM instructions_fts
        JOIN instructions_data ON instructions_data.rowid = instructions_fts.rowid
        WHERE instructions_fts MATCH ?
        ORDER BY bm25(instructions_fts), length(instructions_data.key), instructions_data.key
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    return [InstructionRecord(**msgpack.unpackb(row["payload"], raw=False)) for row in rows]
