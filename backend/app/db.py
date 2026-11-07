import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    account_id TEXT,
    delimiter TEXT,
    encoding TEXT,
    date_format TEXT,
    decimal_sep TEXT,
    invert_sign INTEGER NOT NULL DEFAULT 0,
    columns TEXT NOT NULL,
    options TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    name TEXT,
    priority INTEGER NOT NULL DEFAULT 100,
    field TEXT NOT NULL DEFAULT 'any',
    match_type TEXT NOT NULL DEFAULT 'contains',
    pattern TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'any',
    account_id TEXT,
    amount_min REAL,
    amount_max REAL,
    set_category TEXT,
    set_budget TEXT,
    set_tags TEXT,
    set_counterparty TEXT,
    set_description TEXT,
    set_transfer_account_id TEXT,
    set_skip INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    hits INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    filename TEXT,
    profile_id INTEGER,
    account_id TEXT,
    chat_id INTEGER,
    status TEXT NOT NULL DEFAULT 'review',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rows (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    date TEXT NOT NULL,
    amount TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    counterparty TEXT,
    notes TEXT,
    account_id TEXT,
    category TEXT,
    budget TEXT,
    tags TEXT,
    transfer_account_id TEXT,
    hash TEXT NOT NULL,
    status TEXT NOT NULL,
    dup_reason TEXT,
    rule_id INTEGER,
    cat_source TEXT,
    firefly_id TEXT,
    error TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS rows_batch ON rows(batch_id);

CREATE TABLE IF NOT EXISTS imported_hashes (
    hash TEXT PRIMARY KEY,
    firefly_id TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    alias TEXT NOT NULL,
    value TEXT NOT NULL,
    UNIQUE(kind, alias)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

MIGRATIONS = [
    ("profiles", "options", "TEXT"),
    ("rows", "cat_source", "TEXT"),
    ("rules", "set_skip", "INTEGER NOT NULL DEFAULT 0"),
]

JSON_FIELDS = {"columns", "options", "tags", "set_tags", "raw"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db() -> None:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.uploads_dir.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Migrazioni additive per database creati da versioni precedenti
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(get_settings().db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_FIELDS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    return d


def to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [to_dict(r) for r in rows]  # type: ignore[misc]


def encode(values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in values.items():
        out[k] = json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and v is not None else v
    return out


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    values = encode(values)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
    return int(cur.lastrowid)  # type: ignore[arg-type]


def update(conn: sqlite3.Connection, table: str, id_: int, values: dict[str, Any]) -> None:
    if not values:
        return
    values = encode(values)
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), id_])


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
