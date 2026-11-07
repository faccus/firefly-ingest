from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import db, pipeline
from .firefly import FireflyError, get_firefly
from .quicktext import QuickTextError
from .tabular import TableError, distinct_values, guess_header_row, guess_mapping, headers_at

router = APIRouter(prefix="/api")

MAX_UPLOAD = 20 * 1024 * 1024


# ---------- Modelli ----------


class ProfileIn(BaseModel):
    name: str
    account_id: str | None = None
    delimiter: str | None = None
    encoding: str | None = None
    date_format: str | None = None
    decimal_sep: Literal[",", "."] | None = None
    invert_sign: bool = False
    columns: dict[str, Any]
    options: dict[str, Any] | None = None


class RuleIn(BaseModel):
    name: str | None = None
    priority: int = 100
    field: Literal["any", "description", "counterparty", "notes"] = "any"
    match_type: Literal["contains", "equals", "starts", "regex"] = "contains"
    pattern: str = Field(min_length=1)
    direction: Literal["any", "in", "out"] = "any"
    account_id: str | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    set_category: str | None = None
    set_budget: str | None = None
    set_tags: list[str] | None = None
    set_counterparty: str | None = None
    set_description: str | None = None
    set_transfer_account_id: str | None = None
    set_skip: bool = False
    active: bool = True


class RowPatch(BaseModel):
    date: str | None = None
    amount: str | None = None
    description: str | None = None
    counterparty: str | None = None
    notes: str | None = None
    account_id: str | None = None
    category: str | None = None
    budget: str | None = None
    tags: list[str] | None = None
    transfer_account_id: str | None = None
    status: Literal["new", "skipped"] | None = None


class AliasIn(BaseModel):
    kind: Literal["account", "category"]
    alias: str = Field(min_length=1)
    value: str = Field(min_length=1)


class TextIn(BaseModel):
    text: str
    account_id: str | None = None


class ImportIn(BaseModel):
    row_ids: list[int] | None = None


class SettingsIn(BaseModel):
    default_account_id: str | None = None


# ---------- Meta / impostazioni ----------


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta")
async def meta(refresh: bool = False) -> dict[str, Any]:
    ff = get_firefly()
    try:
        m = await ff.metadata(force=refresh)
        return {**m, "firefly_ok": True, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"accounts": [], "categories": [], "budgets": [], "tags": [], "firefly_ok": False, "error": str(e)}


async def _meta_or_none() -> dict[str, Any] | None:
    try:
        return await get_firefly().metadata()
    except Exception:  # noqa: BLE001
        return None


@router.get("/settings")
def get_settings_() -> dict[str, Any]:
    with db.connect() as conn:
        return {"default_account_id": db.get_setting(conn, "default_account_id")}


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    with db.connect() as conn:
        db.set_setting(conn, "default_account_id", body.default_account_id or None)
    return get_settings_()


# ---------- Profili ----------


@router.get("/profiles")
def list_profiles() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return db.to_dicts(conn.execute("SELECT * FROM profiles ORDER BY name").fetchall())


@router.post("/profiles")
def create_profile(body: ProfileIn) -> dict[str, Any]:
    with db.connect() as conn:
        pid = db.insert(conn, "profiles", {**body.model_dump(), "invert_sign": int(body.invert_sign), "created_at": db.now_iso()})
        return db.to_dict(conn.execute("SELECT * FROM profiles WHERE id = ?", (pid,)).fetchone())  # type: ignore[return-value]


@router.put("/profiles/{pid}")
def update_profile(pid: int, body: ProfileIn) -> dict[str, Any]:
    with db.connect() as conn:
        db.update(conn, "profiles", pid, {**body.model_dump(), "invert_sign": int(body.invert_sign)})
        r = db.to_dict(conn.execute("SELECT * FROM profiles WHERE id = ?", (pid,)).fetchone())
    if not r:
        raise HTTPException(404, "Profilo non trovato")
    return r


@router.delete("/profiles/{pid}")
def delete_profile(pid: int) -> dict[str, bool]:
    with db.connect() as conn:
        conn.execute("DELETE FROM profiles WHERE id = ?", (pid,))
    return {"ok": True}


# ---------- Regole ----------


def _rule_values(body: RuleIn) -> dict[str, Any]:
    v = body.model_dump()
    v["active"] = int(body.active)
    v["set_skip"] = int(body.set_skip)
    for k in ("set_category", "set_budget", "set_counterparty", "set_description", "set_transfer_account_id", "account_id", "name"):
        if v[k] == "":
            v[k] = None
    if not v["set_tags"]:
        v["set_tags"] = None
    return v


@router.get("/rules")
def list_rules() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return db.to_dicts(conn.execute("SELECT * FROM rules ORDER BY priority, id").fetchall())


@router.post("/rules")
def create_rule(body: RuleIn) -> dict[str, Any]:
    with db.connect() as conn:
        rid = db.insert(conn, "rules", {**_rule_values(body), "created_at": db.now_iso()})
        return db.to_dict(conn.execute("SELECT * FROM rules WHERE id = ?", (rid,)).fetchone())  # type: ignore[return-value]


@router.put("/rules/{rid}")
def update_rule(rid: int, body: RuleIn) -> dict[str, Any]:
    with db.connect() as conn:
        db.update(conn, "rules", rid, _rule_values(body))
        r = db.to_dict(conn.execute("SELECT * FROM rules WHERE id = ?", (rid,)).fetchone())
    if not r:
        raise HTTPException(404, "Regola non trovata")
    return r


@router.delete("/rules/{rid}")
def delete_rule(rid: int) -> dict[str, bool]:
    with db.connect() as conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rid,))
    return {"ok": True}


# ---------- Alias ----------


@router.get("/aliases")
def list_aliases() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return db.to_dicts(conn.execute("SELECT * FROM aliases ORDER BY kind, alias").fetchall())


@router.post("/aliases")
def create_alias(body: AliasIn) -> dict[str, Any]:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO aliases(kind, alias, value) VALUES (?, ?, ?) ON CONFLICT(kind, alias) DO UPDATE SET value = excluded.value",
            (body.kind, body.alias.lower(), body.value),
        )
    return {"ok": True}


@router.delete("/aliases/{aid}")
def delete_alias(aid: int) -> dict[str, bool]:
    with db.connect() as conn:
        conn.execute("DELETE FROM aliases WHERE id = ?", (aid,))
    return {"ok": True}


# ---------- Caricamento ----------


async def _finish_upload(result: dict[str, Any]) -> dict[str, Any]:
    if "batch_id" in result:
        with db.connect() as conn:
            await pipeline.enrich_batch(conn, get_firefly(), result["batch_id"])
    return result


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    profile_id: int | None = Form(None),
    account_id: str | None = Form(None),
) -> dict[str, Any]:
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File troppo grande")
    upload_id = pipeline.save_upload(content, file.filename or "file.csv")
    try:
        with db.connect() as conn:
            result = pipeline.process_upload(conn, upload_id, profile_id, account_id or None)
    except TableError as e:
        raise HTTPException(422, str(e)) from e
    return await _finish_upload(result)


@router.get("/uploads/{upload_id}/inspect")
def inspect_upload(upload_id: str) -> dict[str, Any]:
    try:
        table, name = pipeline.read_upload_table(upload_id)
    except FileNotFoundError as e:
        raise HTTPException(404, "Caricamento non trovato") from e
    except TableError as e:
        raise HTTPException(422, str(e)) from e
    idx = guess_header_row(table.rows)
    headers = headers_at(table.rows, idx)
    sample = [[("" if c is None else str(c)) for c in r] for r in table.rows[idx + 1 : idx + 9]]
    return {
        "upload_id": upload_id,
        "filename": name,
        "header_row": idx,
        "headers": headers,
        "sample": sample,
        "guess": guess_mapping(headers),
        "distinct": distinct_values(table, idx),
        "encoding": table.encoding,
        "delimiter": table.delimiter,
    }


class ProcessIn(BaseModel):
    profile_id: int
    account_id: str | None = None


@router.post("/uploads/{upload_id}/process")
async def process(upload_id: str, body: ProcessIn) -> dict[str, Any]:
    try:
        with db.connect() as conn:
            result = pipeline.process_upload(conn, upload_id, body.profile_id, body.account_id or None)
    except FileNotFoundError as e:
        raise HTTPException(404, "Caricamento non trovato") from e
    except TableError as e:
        raise HTTPException(422, str(e)) from e
    return await _finish_upload(result)


@router.post("/text")
async def quick_text(body: TextIn) -> dict[str, Any]:
    meta = await _meta_or_none()
    try:
        with db.connect() as conn:
            bid = pipeline.text_to_batch(conn, body.text, meta, account_id=body.account_id or None)
    except (QuickTextError, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    with db.connect() as conn:
        await pipeline.suggest_from_history(conn, get_firefly(), bid)
    return {"batch_id": bid}


# ---------- Lotti ----------


@router.get("/batches")
def list_batches() -> list[dict[str, Any]]:
    with db.connect() as conn:
        batches = db.to_dicts(conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 200").fetchall())
        for b in batches:
            b["counts"] = pipeline.batch_counts(conn, b["id"])
    return batches


@router.get("/batches/{bid}")
def get_batch(bid: int) -> dict[str, Any]:
    with db.connect() as conn:
        b = db.to_dict(conn.execute("SELECT * FROM batches WHERE id = ?", (bid,)).fetchone())
        if not b:
            raise HTTPException(404, "Lotto non trovato")
        b["rows"] = db.to_dicts(conn.execute("SELECT * FROM rows WHERE batch_id = ? ORDER BY idx", (bid,)).fetchall())
        b["counts"] = pipeline.batch_counts(conn, bid)
    return b


@router.delete("/batches/{bid}")
def delete_batch(bid: int) -> dict[str, bool]:
    with db.connect() as conn:
        conn.execute("DELETE FROM batches WHERE id = ?", (bid,))
    return {"ok": True}


@router.post("/batches/{bid}/reapply-rules")
def reapply(bid: int) -> dict[str, int]:
    with db.connect() as conn:
        return {"changed": pipeline.reapply_rules(conn, bid)}


class PreviewIn(BaseModel):
    row_ids: list[int]


@router.post("/batches/{bid}/preview")
async def preview(bid: int, body: PreviewIn) -> dict[str, Any]:
    meta = await _meta_or_none()
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM batches WHERE id = ?", (bid,)).fetchone():
            raise HTTPException(404, "Lotto non trovato")
        return await pipeline.preview_rows(conn, get_firefly(), bid, body.row_ids, meta)


@router.post("/batches/{bid}/import")
async def do_import(bid: int, body: ImportIn) -> dict[str, int]:
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM batches WHERE id = ?", (bid,)).fetchone():
            raise HTTPException(404, "Lotto non trovato")
        result = await pipeline.import_rows(conn, get_firefly(), bid, body.row_ids)
    get_firefly().invalidate()  # categorie/tag nuovi
    return result


@router.patch("/rows/{row_id}")
def patch_row(row_id: int, body: RowPatch) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    for k in ("category", "budget", "counterparty", "notes", "transfer_account_id", "account_id"):
        if values.get(k) == "":
            values[k] = None
    with db.connect() as conn:
        r = db.to_dict(conn.execute("SELECT * FROM rows WHERE id = ?", (row_id,)).fetchone())
        if not r:
            raise HTTPException(404, "Riga non trovata")
        if r["status"] == pipeline.IMPORTED:
            raise HTTPException(409, "Riga già importata")
        if values.get("status") == "new" and r["status"] in (pipeline.DUPLICATE, pipeline.POSSIBLE_DUP):
            values["status"] = pipeline.POSSIBLE_DUP if r["status"] == pipeline.POSSIBLE_DUP else pipeline.READY
        if "category" in values:
            values["cat_source"] = "manual" if values["category"] else None
        db.update(conn, "rows", row_id, values)
        return db.to_dict(conn.execute("SELECT * FROM rows WHERE id = ?", (row_id,)).fetchone())  # type: ignore[return-value]


@router.get("/firefly/check")
async def firefly_check() -> dict[str, Any]:
    try:
        about = await get_firefly().about()
        return {"ok": True, "version": about.get("version"), "api_version": about.get("api_version")}
    except FireflyError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
