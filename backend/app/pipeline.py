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
            "orig_description": rec.description,
            "orig_counterparty": rec.counterparty,
            "bank_category": rec.bank_category,
        }
        # campi imposti dall'utente (es. #categoria/@conto/!tag nel testo rapido): le regole non li toccano
        for k, v in ov.items():
            if v not in (None, "", []):
                row[k] = v
        row["manual"] = [k for k in RULE_FIELDS if k in ov and ov[k] not in (None, "", [])]
        derived, skip = derive_row(rules, row, use_bank_category)
        row.update(derived)
        if skip:
            row["status"], row["dup_reason"] = SKIPPED, f"Ignorata dalla regola #{row['rule_id']}"
        if row["rule_id"]:
            hit_counts[row["rule_id"]] += 1
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


FORCEABLE = (DUPLICATE, SKIPPED)
IMPORTABLE = (READY, POSSIBLE_DUP, ERROR)

TYPE_LABEL = {"withdrawal": "uscita", "deposit": "entrata", "transfer": "giroconto"}


async def preview_rows(
    conn: sqlite3.Connection, ff: FireflyClient, batch_id: int, row_ids: list[int], meta: dict[str, Any] | None
) -> dict[str, Any]:
    """Cosa verrà creato in Firefly per le righe selezionate, costruito con build_split
    (la stessa funzione usata dall'invio), più avvisi e totali."""
    meta = meta or {}
    names = {a["id"]: a["name"] for a in meta.get("accounts", [])}
    known_cats = {c.casefold() for c in meta.get("categories", [])}
    known_tags = {t.casefold() for t in meta.get("tags", [])}
    known_budgets = {b.casefold() for b in meta.get("budgets", [])}
    expense = {a.casefold() for a in meta.get("expense_accounts", [])}
    revenue = {a.casefold() for a in meta.get("revenue_accounts", [])}
    wanted = set(row_ids)
    rows = [
        r
        for r in db.to_dicts(conn.execute("SELECT * FROM rows WHERE batch_id = ? ORDER BY idx", (batch_id,)).fetchall())
        if r["id"] in wanted and r["status"] != IMPORTED
    ]
    items: list[dict[str, Any]] = []
    new_categories: set[str] = set()
    new_tags: set[str] = set()
    new_parties: set[str] = set()
    by_type: Counter[str] = Counter()
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for r in rows:
        warnings: list[str] = []
        errors: list[str] = []
        if not r["account_id"]:
            errors.append("conto non impostato")
            items.append({"row_id": r["id"], "errors": errors, "warnings": warnings, "date": r["date"],
                          "amount": r["amount"], "description": r["description"]})
            continue
        split = build_split(r)
        t = split["type"]
        acc_name = names.get(str(r["account_id"]), f"#{r['account_id']}")
        party = split.get("destination_name") or split.get("source_name")
        if t == "transfer":
            src, dst = names.get(split["source_id"], f"#{split['source_id']}"), names.get(split["destination_id"], f"#{split['destination_id']}")
            existing = await find_existing_transfer(ff, r)
            if existing:
                warnings.append(f"{existing}: non verrà creato")
        elif t == "withdrawal":
            src, dst = acc_name, party or "(senza controparte)"
            if party and party.casefold() not in expense:
                new_parties.add(party)
        else:
            src, dst = party or "(senza controparte)", acc_name
            if party and party.casefold() not in revenue:
                new_parties.add(party)
        if r["status"] == DUPLICATE:
            warnings.append(f"duplicato incluso a mano ({r['dup_reason']})")
        elif r["status"] == POSSIBLE_DUP:
            warnings.append(f"possibile duplicato ({r['dup_reason']})")
        elif r["status"] == SKIPPED:
            warnings.append("riga esclusa, inclusa a mano")
        elif r["status"] == ERROR and r["error"]:
            warnings.append(f"errore precedente: {r['error']}")
        cat = split.get("category_name")
        if t != "transfer" and not cat:
            warnings.append("senza categoria")
        if cat and cat.casefold() not in known_cats:
            new_categories.add(cat)
        if split.get("budget_name") and split["budget_name"].casefold() not in known_budgets:
            warnings.append(f"budget inesistente: {split['budget_name']}")
        for tag in split.get("tags") or []:
            if tag.casefold() not in known_tags:
                new_tags.add(tag)
        by_type[t] += 1
        totals[t] += Decimal(split["amount"])
        items.append(
            {
                "row_id": r["id"],
                "type": t,
                "type_label": TYPE_LABEL[t],
                "date": split["date"],
                "amount": split["amount"],
                "source": src,
                "destination": dst,
                "description": split["description"],
                "category": cat,
                "budget": split.get("budget_name"),
                "tags": split.get("tags") or [],
                "notes": split.get("notes"),
                "forced": r["status"] in FORCEABLE,
                "warnings": warnings,
                "errors": errors,
            }
        )
    return {
        "items": items,
        "summary": {
            "count": sum(by_type.values()),
            "by_type": {TYPE_LABEL[k]: {"count": v, "total": str(totals[k])} for k, v in by_type.items()},
            "errors": sum(1 for i in items if i["errors"]),
            "warnings": sum(1 for i in items if i["warnings"]),
        },
        "new_categories": sorted(new_categories),
        "new_tags": sorted(new_tags),
        "new_counterparties": sorted(new_parties),
        "firefly_ok": bool(meta),
    }


async def import_rows(conn: sqlite3.Connection, ff: FireflyClient, batch_id: int, row_ids: list[int] | None = None) -> dict[str, int]:
    rows = db.to_dicts(conn.execute("SELECT * FROM rows WHERE batch_id = ? ORDER BY idx", (batch_id,)).fetchall())
    if row_ids is not None:
        # selezione esplicita (dopo l'anteprima): include anche duplicati ed escluse scelti a mano
        wanted = set(row_ids)
        rows = [r for r in rows if r["id"] in wanted and r["status"] in (*IMPORTABLE, *FORCEABLE)]
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


RULE_FIELDS = ("category", "budget", "tags", "transfer_account_id", "description", "counterparty")


def derive_row(rules: list[dict[str, Any]], row: dict[str, Any], use_bank_category: bool = False) -> tuple[dict[str, Any], bool]:
    """Ricalcola da zero i campi che dipendono dalle regole, partendo dai valori
    originali del file e conservando quelli modificati a mano (row["manual"]).
    Ritorna (campi, ignora)."""
    manual = set(row.get("manual") or [])
    base = {**row}
    has_orig = row.get("orig_description") is not None
    if "description" not in manual and has_orig:
        base["description"] = row["orig_description"]
    if "counterparty" not in manual and has_orig:
        base["counterparty"] = row.get("orig_counterparty")
    for k in ("category", "budget", "transfer_account_id"):
        if k not in manual:
            base[k] = None
    if "tags" not in manual:
        base["tags"] = []
    changes, rule_id = apply_rules(rules, base)
    skip = bool(changes.pop("skip", False))
    out = {k: base.get(k) for k in RULE_FIELDS}
    out.update(changes)
    for k in manual & set(RULE_FIELDS):
        out[k] = row.get(k)  # le modifiche manuali vincono sempre
    if "category" in manual:
        out["cat_source"] = "manual"
    elif changes.get("category"):
        out["cat_source"] = "rule"
    elif use_bank_category and row.get("bank_category") and not out["transfer_account_id"]:
        out["category"], out["cat_source"] = row["bank_category"], "bank"
    else:
        out["cat_source"] = None
    out["rule_id"] = rule_id
    return out, skip


def reapply_rules(conn: sqlite3.Connection, batch_id: int) -> int:
    """Riapplica tutte le regole attive alle righe non ancora importate: i campi
    derivati dalle regole vengono ricalcolati, le modifiche manuali restano.
    Ritorna il numero di righe cambiate."""
    rules = active_rules(conn)
    b = conn.execute("SELECT profile_id FROM batches WHERE id = ?", (batch_id,)).fetchone()
    use_bank = False
    if b and b["profile_id"]:
        p = db.to_dict(conn.execute("SELECT options FROM profiles WHERE id = ?", (b["profile_id"],)).fetchone())
        use_bank = bool(((p or {}).get("options") or {}).get("use_bank_category"))
    rows = db.to_dicts(
        conn.execute("SELECT * FROM rows WHERE batch_id = ? AND status NOT IN (?, ?)", (batch_id, IMPORTED, DUPLICATE)).fetchall()
    )
    changed = 0
    for r in rows:
        derived, skip = derive_row(rules, r, use_bank)
        if skip and r["status"] in (READY, POSSIBLE_DUP, ERROR, SKIPPED):
            derived["status"], derived["dup_reason"] = SKIPPED, f"Ignorata dalla regola #{derived['rule_id']}"
        elif not skip and r["status"] == SKIPPED and (r["dup_reason"] or "").startswith("Ignorata dalla regola"):
            derived["status"], derived["dup_reason"] = READY, None
        diff = {k: v for k, v in derived.items() if r.get(k) != v}
        if diff:
            db.update(conn, "rows", r["id"], diff)
            if set(diff) - {"rule_id", "cat_source"}:
                changed += 1
    return changed
