import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import router
from .bot import build_application
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("firefly_ingest")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    bot = build_application()
    if bot:
        await bot.initialize()
        await bot.start()
        await bot.updater.start_polling(drop_pending_updates=False)  # type: ignore[union-attr]
        log.info("Bot Telegram avviato")
    else:
        log.info("TELEGRAM_BOT_TOKEN non impostato: bot disabilitato")
    if not get_settings().firefly_token:
        log.warning("FIREFLY_TOKEN non impostato: l'invio a Firefly non funzionerà")
    try:
        yield
    finally:
        if bot:
            await bot.updater.stop()  # type: ignore[union-attr]
            await bot.stop()
            await bot.shutdown()


app = FastAPI(title="Firefly Ingest", lifespan=lifespan)
app.include_router(router)

static = get_settings().static_dir
if (static / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):  # type: ignore[no-untyped-def]
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    f = static / path
    if path and f.is_file() and static.resolve() in f.resolve().parents:
        return FileResponse(f)
    index = static / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend non compilato"}, status_code=404)
