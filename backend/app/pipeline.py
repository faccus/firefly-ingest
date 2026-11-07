"""Dal dato grezzo alla transazione Firefly: staging, deduplica, regole, invio."""

import hashlib
import logging
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from . import db, history
from .config import get_settings
from .firefly import FireflyClient, FireflyError
from .quicktext import parse_text
from .rules import apply_rules
from .tabular import Record, Table, TableError, detect_profile, extract_records, read_table
from .values import normalize_for_hash

log = logging.getLogger(__name__)

READY = "new"
DUPLICATE = "duplicate"
POSSIBLE_DUP = "possible_duplicate"
SKIPPED = "skipped"
IMPORTED = "imported"
ERROR = "error"


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().tz)).date()


def row_hash(account_id: str | None, d: date, amount: Decimal, description: str, occurrence: int, salt: str = "") -> str:
    base = f"{account_id}|{d.isoformat()}|{amount}|{normalize_for_hash(description)}|{occurrence}|{salt}"
    return "ffi-" + hashlib.sha256(base.encode()).hexdigest()[:24]


def active_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.to_dicts(conn.execute("SELECT * FROM rules WHERE active = 1").fetchall())


def create_batch(
    conn: sqlite3.Connection,
    *,
    source: str,
    records: list[Record],
    account_id: str | None,
    filename: str | None = None,
    profile_id: int | None = None,
    chat_id: int | None = None,
    unique: bool = False,
    overrides: list[dict[str, Any]] | None = None,
    use_bank_category: bool = False,
) -> int:
    """Crea un lotto in revisione.

    unique=True (inserimento manuale) rende l'hash univoco: due "caffè 1.20 oggi"
    sono due caffè, non un duplicato.
    overrides: campi per-riga imposti dall'utente (es. conto/categoria da testo rapido),
    che hanno precedenza sulle regole.
    """
    batch_id = db.insert(
        conn,
        "batches",
        {
            "source": source,
            "filename": filename,
            "profile_id": profile_id,
            "account_id": account_id,
            "chat_id": chat_id,
            "status": "review",
            "created_at": db.now_iso(),
        },
    )
    rules = active_rules(conn)
    occurrences: Counter[str] = Counter()
    salt = uuid.uuid4().hex if unique else ""
    hit_counts: Counter[int] = Counter()
    for i, rec in enumerate(records):
        ov = (overrides[i] if overrides else None) or {}
        acc = ov.get("account_id") or rec.account_id or account_id
        key = f"{acc}|{rec.date}|{rec.amount}|{normalize_for_hash(rec.description)}"
        occ = occurrences[key]
        occurrences[key] += 1
        h = row_hash(acc, rec.date, rec.amount, rec.description, occ, salt)
        row: dict[str, Any] = {
            "batch_id": batch_id,
            "idx": i,
            "date": rec.date.isoformat(),
            "amount": str(rec.amount),
            "description": rec.description,
            "counterparty": rec.counterparty,
            "notes": rec.notes,
            "account_id": acc,
            "category": None,
            "budget": None,
            "tags": [],
            "transfer_account_id": None,
            "hash": h,
            "status": READY,
            "raw": rec.raw or None,
        }
        changes, rule_id = apply_rules(rules, row)
        skip = changes.pop("skip", False)
        row.update(changes)
        row["rule_id"] = rule_id
        if skip:
            row["status"], row["dup_reason"] = SKIPPED, f"Ignorata dalla regola #{rule_id}"
        if rule_id:
            hit_counts[rule_id] += 1
        if changes.get("category"):
            row["cat_source"] = "rule"
        for k, v in ov.items():
            if v not in (None, "", []):
                row[k] = v
                if k == "category":
                    row["cat_source"] = "manual"
        if use_bank_category and rec.bank_category and not row["category"] and not row["transfer_account_id"]:
            row["category"], row["cat_source"] = rec.bank_category, "bank"
        if conn.execute("SELECT 1 FROM imported_hashes WHERE hash = ?", (h,)).fetchone():
            row["status"] = DUPLICATE
            row["dup_reason"] = "Già importata da questa app"
        db.insert(conn, "rows", row)
    for rid, n in hit_counts.items():
        conn.execute("UPDATE rules SET hits = hits + ? WHERE id = ?", (n, rid))
    return batch_id


async def check_firefly_duplicates(conn: sqlite3.Connection, ff: FireflyClient, batch_id: int) -> int:
    """Segna come possibili duplicati le righe che hanno già una transazione in Firefly
    sullo stesso conto con stessa data (±1 giorno) e stesso importo."""
    rows = db.to_dicts(
        conn.execute("SELECT * FROM rows WHERE batch_id = ? AND status = ?", (batch_id, READY)).fetchall()
    )
    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["account_id"]:
            by_account[str(r["account_id"])].append(r)
    marked = 0
    for acc, acc_rows in by_account.items():
        dates = [date.fromisoformat(r["date"]) for r in acc_rows]
        try:
            existing = await ff.account_transactions(acc, min(dates) - timedelta(days=1), max(dates) + timedelta(days=1))
        except FireflyError as e:
            log.warning("Controllo duplicati Firefly fallito per il conto %s: %s", acc, e)
            continue
        by_ext = {e.external_id: e for e in existing if e.external_id}
        pool: dict[Decimal, list] = defaultdict(list)
        for e in existing:
            pool[e.amount].append(e)
        for r in acc_rows:
            if r["hash"] in by_ext:
                db.update(conn, "rows", r["id"], {"status": DUPLICATE, "dup_reason": f"Già in Firefly (#{by_ext[r['hash']].group_id})"})
                marked += 1
                continue
            amt = Decimal(r["amount"])
            d = date.fromisoformat(r["date"])
            cands = [e for e in pool.get(amt, []) if abs((date.fromisoformat(e.date) - d).days) <= 1]
            if cands:
                e = cands[0]
                pool[amt].remove(e)
                db.update(
                    conn,
                    "rows",
                    r["id"],
                    {"status": POSSIBLE_DUP, "dup_reason": f"Simile a Firefly #{e.group_id}: {e.date} {e.description}"},
                )
                marked += 1
    return marked


async def suggest_from_history(conn: sqlite3.Connection, ff: FireflyClient, batch_id: int) -> int:
    """Propone la categoria dalle transazioni simili in Firefly per le righe che non ne hanno
    (o hanno solo quella della banca). Ritorna il numero di righe aggiornate."""
    rows = db.to_dicts(
        conn.execute(
            "SELECT * FROM rows WHERE batch_id = ? AND status NOT IN (?, ?) AND transfer_account_id IS NULL "
            "AND (category IS NULL OR cat_source = 'bank')",
            (batch_id, IMPORTED, DUPLICATE),
        ).fetchall()
    )
    if not rows:
        return 0
    try:
        idx = await history.get_index(ff)
    except Exception as e:  # noqa: BLE001
        log.warning("Storico Firefly non disponibile: %s", e)
        return 0
    n = 0
    for r in rows:
        s = idx.suggest(r["description"], r["counterparty"])
        if s:
            # il motivo del suggerimento resta nel raw, visibile in revisione
            raw = {**(r["raw"] or {}), "_suggerimento": s[1]}
            db.update(conn, "rows", r["id"], {"category": s[0], "cat_source": "history", "raw": raw})
            n += 1
    return n


async def enrich_batch(conn: sqlite3.Connection, ff: FireflyClient, batch_id: int) -> None:
    """Controlli che richiedono Firefly: duplicati e suggerimenti dallo storico."""
    await check_firefly_duplicates(conn, ff, batch_id)
    await suggest_from_history(conn, ff, batch_id)


# ---------- File ----------


def save_upload(content: bytes, filename: str) -> str:
    upload_id = uuid.uuid4().hex
    d = get_settings().uploads_dir / upload_id
    d.mkdir(parents=True)
    safe = "".join(c for c in filename if c.isalnum() or c in "._-") or "file"
    (d / safe).write_bytes(content)
    return upload_id


def load_upload(upload_id: str) -> tuple[bytes, str]:
    if not upload_id.isalnum():
        raise FileNotFoundError(upload_id)
    d = get_settings().uploads_dir / upload_id
    files = list(d.iterdir()) if d.is_dir() else []
    if not files:
        raise FileNotFoundError(upload_id)
    return files[0].read_bytes(), files[0].name


def read_upload_table(upload_id: str, profile: dict[str, Any] | None = None) -> tuple[Table, str]:
    content, name = load_upload(upload_id)
    table = read_table(
        content,
        name,
        encoding=(profile or {}).get("encoding") or None,
        delimiter=(profile or {}).get("delimiter") or None,
    )
    return table, name


def process_upload(
    conn: sqlite3.Connection,
    upload_id: str,
    profile_id: int | None = None,
    account_id: str | None = None,
    source: str = "web",
    chat_id: int | None = None,
) -> dict[str, Any]:
    """Ritorna {"batch_id": ...} oppure {"needs_profile": True, ...} se il formato è sconosciuto."""
    profiles = db.to_dicts(conn.execute("SELECT * FROM profiles").fetchall())
    table, name = read_upload_table(upload_id)
    profile = None
    if profile_id is not None:
        profile = next((p for p in profiles if p["id"] == profile_id), None)
        if profile is None:
            raise TableError("Profilo inesistente")
    else:
        profile = detect_profile(table.rows, profiles)
    if profile is None:
        return {"needs_profile": True, "upload_id": upload_id, "filename": name}
    if profile.get("encoding") or profile.get("delimiter"):
        table, name = read_upload_table(upload_id, profile)
    records = extract_records(table, profile)
    if not records:
        raise TableError("Nessun movimento trovato nel file con questo profilo")
    acc = account_id or profile.get("account_id") or db.get_setting(conn, "default_account_id")
    options = profile.get("options") or {}
    batch_id = create_batch(
        conn,
        source=source,
        records=records,
        account_id=acc,
        filename=name,
        profile_id=profile["id"],
        chat_id=chat_id,
        use_bank_category=bool(options.get("use_bank_category")),
    )
    return {"batch_id": batch_id, "profile": profile["name"], "rows": len(records)}


# ---------- Testo rapido ----------


def resolve_alias(conn: sqlite3.Connection, kind: str, value: str | None, choices: list[str] | None = None) -> str | None:
    if not value:
        return None
    r = conn.execute("SELECT value FROM aliases WHERE kind = ? AND lower(alias) = lower(?)", (kind, value)).fetchone()
    if r:
        return r["value"]
    if choices:
        for c in choices:
            if c.casefold() == value.casefold():
                return c
        for c in choices:
            if c.casefold().startswith(value.casefold()):
                return c
    return value if kind == "category" else None


def text_to_batch(
    conn: sqlite3.Connection,
    text: str,
    meta: dict[str, Any] | None,
    source: str = "web",
    chat_id: int | None = None,
    account_id: str | None = None,
) -> int:
    entries = parse_text(text, today())
    accounts = (meta or {}).get("accounts", [])
    by_name = {a["name"]: a["id"] for a in accounts}
    default_acc = account_id or db.get_setting(conn, "default_account_id")
    records, overrides = [], []
    for e in entries:
        acc = None
        if e.account:
            resolved = resolve_alias(conn, "account", e.account, list(by_name))
            if resolved is None:
                raise ValueError(f"Conto sconosciuto: @{e.account}")
            acc = by_name.get(resolved, resolved)
        cat = resolve_alias(conn, "category", e.category, (meta or {}).get("categories"))
        records.append(Record(date=e.date, amount=e.amount, description=e.description))
        overrides.append({"account_id": acc, "category": cat, "tags": e.tags})
    if not default_acc and any(not o["account_id"] for o in overrides):
        raise ValueError("Nessun conto indicato: usa @conto oppure imposta un conto predefinito")
    return create_batch(
        conn, source=source, records=records, account_id=default_acc, chat_id=chat_id, unique=True, overrides=overrides
    )


# ---------- Invio a Firefly ----------


def build_split(row: dict[str, Any]) -> dict[str, Any]:
    amount = Decimal(row["amount"])
    acc = str(row["account_id"])
    split: dict[str, Any] = {
        "date": row["date"],
        "amount": str(abs(amount)),
        "description": row["description"] or "(senza descrizione)",
        "external_id": row["hash"],
    }
    other = row.get("counterparty") or None
    if row.get("transfer_account_id"):
        split["type"] = "transfer"
        t = str(row["transfer_account_id"])
        split["source_id"], split["destination_id"] = (acc, t) if amount < 0 else (t, acc)
    elif amount < 0:
        split["type"] = "withdrawal"
        split["source_id"] = acc
        if other:
            split["destination_name"] = other
        if row.get("budget"):
            split["budget_name"] = row["budget"]
    else:
        split["type"] = "deposit"
        split["destination_id"] = acc
        if other:
            split["source_name"] = other
    if row.get("category") and not row.get("transfer_account_id"):
        split["category_name"] = row["category"]
    if row.get("tags"):
        split["tags"] = row["tags"]
    if row.get("notes"):
        split["notes"] = row["notes"]
    return split


async def find_existing_transfer(ff: FireflyClient, row: dict[str, Any]) -> str | None:
    """Un giroconto compare negli estratti di entrambi i conti (es. addebito carta sul conto e
    "ricevuta" sulla carta): se Firefly ha già il giroconto, non va creato di nuovo."""
    d = date.fromisoformat(row["date"])
    amount = Decimal(row["amount"])
    try:
        txs = await ff.account_transactions(str(row["transfer_account_id"]), d - timedelta(days=3), d + timedelta(days=3))
    except FireflyError:
        return None
    for t in txs:
        # visto dal conto controparte l'importo ha segno opposto
        if t.type == "transfer" and t.amount == -amount:
            return f"Giroconto già presente in Firefly (#{t.group_id}, {t.date})"
    return None


async def import_rows(conn: sqlite3.Connection, ff: FireflyClient, batch_id: int, row_ids: list[int] | None = None) -> dict[str, int]:
    q = "SELECT * FROM rows WHERE batch_id = ? AND status IN (?, ?, ?)"
    params: list[Any] = [batch_id, READY, POSSIBLE_DUP, ERROR]
    rows = db.to_dicts(conn.execute(q, params).fetchall())
    if row_ids is not None:
        wanted = set(row_ids)
        rows = [r for r in rows if r["id"] in wanted]
    else:
        # Senza selezione esplicita si importano solo le righe pronte (o in errore da riprovare)
        rows = [r for r in rows if r["status"] in (READY, ERROR)]
    result = Counter({IMPORTED: 0, DUPLICATE: 0, ERROR: 0})
    for r in rows:
        if not r["account_id"]:
            db.update(conn, "rows", r["id"], {"status": ERROR, "error": "Conto non impostato"})
            result[ERROR] += 1
            continue
        if r.get("transfer_account_id"):
            existing = await find_existing_transfer(ff, r)
            if existing:
                db.update(conn, "rows", r["id"], {"status": DUPLICATE, "dup_reason": existing, "error": None})
                result[DUPLICATE] += 1
                continue
        try:
            fid = await ff.create_transaction(build_split(r))
        except FireflyError as e:
            if e.duplicate:
                db.update(conn, "rows", r["id"], {"status": DUPLICATE, "dup_reason": "Rifiutata da Firefly come duplicato", "error": None})
                result[DUPLICATE] += 1
            else:
                db.update(conn, "rows", r["id"], {"status": ERROR, "error": str(e)})
                result[ERROR] += 1
            continue
        except Exception as e:  # noqa: BLE001  (rete, timeout...)
            db.update(conn, "rows", r["id"], {"status": ERROR, "error": f"{type(e).__name__}: {e}"})
            result[ERROR] += 1
            continue
        db.update(conn, "rows", r["id"], {"status": IMPORTED, "firefly_id": fid, "error": None})
        conn.execute(
            "INSERT OR REPLACE INTO imported_hashes(hash, firefly_id, imported_at) VALUES (?, ?, ?)",
            (r["hash"], fid, db.now_iso()),
        )
        conn.commit()
        result[IMPORTED] += 1
    refresh_batch_status(conn, batch_id)
    if result[IMPORTED]:
        history.invalidate()
    return dict(result)


def refresh_batch_status(conn: sqlite3.Connection, batch_id: int) -> str:
    counts = batch_counts(conn, batch_id)
    pending = counts.get(READY, 0) + counts.get(POSSIBLE_DUP, 0) + counts.get(ERROR, 0)
    status = "review" if pending else "done"
    conn.execute("UPDATE batches SET status = ? WHERE id = ?", (status, batch_id))
    return status


def batch_counts(conn: sqlite3.Connection, batch_id: int) -> dict[str, int]:
    return {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, count(*) AS n FROM rows WHERE batch_id = ? GROUP BY status", (batch_id,))
    }


def reapply_rules(conn: sqlite3.Connection, batch_id: int) -> int:
    """Riapplica le regole alle righe non ancora importate.
    Sovrascrive solo i campi ancora vuoti, per non perdere le correzioni manuali."""
    rules = active_rules(conn)
    rows = db.to_dicts(
        conn.execute("SELECT * FROM rows WHERE batch_id = ? AND status NOT IN (?, ?)", (batch_id, IMPORTED, DUPLICATE)).fetchall()
    )
    changed = 0
    for r in rows:
        # i suggerimenti (storico/banca) non sono scelte dell'utente: una regola li sostituisce
        base = {**r}
        if r.get("cat_source") in ("history", "bank"):
            base["category"] = None
        changes, rule_id = apply_rules(rules, base)
        if changes.pop("skip", False) and r["status"] == READY:
            changes["status"], changes["dup_reason"] = SKIPPED, f"Ignorata dalla regola #{rule_id}"
        # descrizione/controparte non vengono riscritte in un riapplica
        changes = {k: v for k, v in changes.items() if k not in ("description", "counterparty") and not base.get(k)}
        if changes.get("category") == r.get("category"):
            changes.pop("category")
        if changes:
            if "category" in changes:
                changes["cat_source"] = "rule"
            changes["rule_id"] = r["rule_id"] or rule_id
            db.update(conn, "rows", r["id"], changes)
            changed += 1
    return changed
