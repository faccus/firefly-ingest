"""Bot Telegram: spese al volo in testo e caricamento di estratti conto."""

import html
import logging
from decimal import Decimal
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import db, pipeline
from .config import get_settings
from .firefly import get_firefly
from .quicktext import QuickTextError
from .tabular import TableError

log = logging.getLogger(__name__)

HELP = (
    "Scrivimi una spesa per riga, ad esempio:\n"
    "<code>12.50 pizza ieri @carta #cene</code>\n"
    "<code>+1800 stipendio 27/10</code>\n\n"
    "• importo: con <b>+</b> è un'entrata\n"
    "• data: oggi, ieri, altroieri, lunedì…, 3/11\n"
    "• <b>@conto</b>, <b>#categoria</b>, <b>!tag</b>\n\n"
    "Puoi anche mandarmi un CSV/XLSX della banca."
)


def _allowed(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id in get_settings().allowed_chat_ids


def _fmt_amount(a: str) -> str:
    d = Decimal(a)
    s = f"{abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'+' if d > 0 else '−'}{s} €"


def _web_link(path: str) -> str | None:
    base = get_settings().public_url.rstrip("/")
    return f"{base}{path}" if base else None


def _keyboard(batch_id: int, n_ready: int) -> InlineKeyboardMarkup:
    buttons = []
    if n_ready:
        buttons.append(InlineKeyboardButton(f"✅ Importa ({n_ready})", callback_data=f"imp:{batch_id}"))
    buttons.append(InlineKeyboardButton("🗑 Annulla", callback_data=f"del:{batch_id}"))
    rows = [buttons]
    link = _web_link(f"/batches/{batch_id}")
    if link:
        rows.append([InlineKeyboardButton("✏️ Rivedi sul web", url=link)])
    return InlineKeyboardMarkup(rows)


def _summary(batch_id: int, accounts: dict[str, str], max_rows: int = 15) -> tuple[str, int]:
    with db.connect() as conn:
        rows = db.to_dicts(conn.execute("SELECT * FROM rows WHERE batch_id = ? ORDER BY idx", (batch_id,)).fetchall())
    ready = [r for r in rows if r["status"] == pipeline.READY]
    lines = []
    for r in rows[:max_rows]:
        mark = {"new": "•", "duplicate": "⛔", "possible_duplicate": "⚠️"}.get(r["status"], "•")
        if r["transfer_account_id"]:
            what = "↔ " + html.escape(accounts.get(str(r["transfer_account_id"]), str(r["transfer_account_id"])))
        elif r["category"]:
            what = html.escape(r["category"])
        else:
            what = "<i>senza categoria</i>"
        acc = html.escape(accounts.get(str(r["account_id"]), str(r["account_id"] or "?")))
        lines.append(
            f"{mark} {r['date'][8:10]}/{r['date'][5:7]} <b>{_fmt_amount(r['amount'])}</b> "
            f"{html.escape(r['description'][:40])} · {what} · {acc}"
        )
    if len(rows) > max_rows:
        lines.append(f"… e altre {len(rows) - max_rows} righe")
    dup = sum(1 for r in rows if r["status"] == pipeline.DUPLICATE)
    poss = sum(1 for r in rows if r["status"] == pipeline.POSSIBLE_DUP)
    footer = [f"{len(ready)} pronte"]
    if dup:
        footer.append(f"{dup} duplicate")
    if poss:
        footer.append(f"{poss} possibili duplicati (da rivedere sul web)")
    return "\n".join(lines) + "\n\n" + ", ".join(footer), len(ready)


async def _accounts() -> tuple[dict[str, Any] | None, dict[str, str]]:
    try:
        meta = await get_firefly().metadata()
    except Exception:  # noqa: BLE001
        return None, {}
    return meta, {a["id"]: a["name"] for a in meta["accounts"]}


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    assert chat and update.message
    if not _allowed(update):
        await update.message.reply_text(
            f"Chat non autorizzata. Aggiungi {chat.id} a TELEGRAM_ALLOWED_CHAT_IDS e riavvia il servizio."
        )
        return
    await update.message.reply_html(HELP)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update) or not update.message or not update.message.text:
        return
    meta, accounts = await _accounts()
    try:
        with db.connect() as conn:
            bid = pipeline.text_to_batch(conn, update.message.text, meta, source="telegram", chat_id=update.effective_chat.id)  # type: ignore[union-attr]
    except (QuickTextError, ValueError) as e:
        await update.message.reply_text(f"❌ {e}\n\nScrivi /aiuto per la sintassi.")
        return
    with db.connect() as conn:
        await pipeline.suggest_from_history(conn, get_firefly(), bid)
    text, n_ready = _summary(bid, accounts)
    await update.message.reply_html(text, reply_markup=_keyboard(bid, n_ready))


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update) or not update.message or not update.message.document:
        return
    doc = update.message.document
    if (doc.file_size or 0) > 20 * 1024 * 1024:
        await update.message.reply_text("File troppo grande")
        return
    f = await doc.get_file()
    content = bytes(await f.download_as_bytearray())
    upload_id = pipeline.save_upload(content, doc.file_name or "file.csv")
    try:
        with db.connect() as conn:
            result = pipeline.process_upload(conn, upload_id, source="telegram", chat_id=update.effective_chat.id)  # type: ignore[union-attr]
            if "batch_id" in result:
                await pipeline.enrich_batch(conn, get_firefly(), result["batch_id"])
    except TableError as e:
        await update.message.reply_text(f"❌ {e}")
        return
    if result.get("needs_profile"):
        link = _web_link(f"/uploads/{upload_id}/profile")
        msg = "Formato non riconosciuto: configura il profilo della banca dalla web app."
        await update.message.reply_text(
            msg,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Configura", url=link)]]) if link else None,
        )
        return
    _, accounts = await _accounts()
    text, n_ready = _summary(result["batch_id"], accounts)
    await update.message.reply_html(
        f"📄 <b>{html.escape(result['profile'])}</b> · {result['rows']} movimenti\n\n{text}",
        reply_markup=_keyboard(result["batch_id"], n_ready),
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not _allowed(update):
        return
    await q.answer()
    action, _, raw_id = (q.data or "").partition(":")
    batch_id = int(raw_id)
    with db.connect() as conn:
        b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not b:
            await q.edit_message_text("Lotto non più disponibile.")
            return
        if action == "del":
            if conn.execute("SELECT 1 FROM rows WHERE batch_id = ? AND status = ?", (batch_id, pipeline.IMPORTED)).fetchone():
                await q.edit_message_text("Lotto già importato in parte: gestiscilo dal web.")
                return
            conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
            await q.edit_message_text("🗑 Annullato.")
            return
        if action == "imp":
            res = await pipeline.import_rows(conn, get_firefly(), batch_id)
    parts = [f"✅ Importate {res['imported']}"]
    if res.get("duplicate"):
        parts.append(f"⛔ duplicate {res['duplicate']}")
    if res.get("error"):
        parts.append(f"❌ errori {res['error']}")
    link = _web_link(f"/batches/{batch_id}")
    await q.edit_message_text(
        (q.message.text_html if q.message else "") + "\n\n" + " · ".join(parts),  # type: ignore[union-attr]
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Apri lotto", url=link)]]) if link and res.get("error") else None,
    )


def build_application() -> Application | None:
    s = get_settings()
    if not s.telegram_bot_token:
        return None
    app = Application.builder().token(s.telegram_bot_token).build()
    app.add_handler(CommandHandler(["start", "aiuto", "help"], cmd_start))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app
