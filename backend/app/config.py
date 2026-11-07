from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # URL interno di Firefly III (DNS Docker sulla rete dello stack)
    firefly_url: str = "http://app:8080"
    # Personal Access Token di Firefly (Profilo -> OAuth -> Personal Access Tokens)
    firefly_token: str = ""
    # Se true, Firefly applica anche le sue regole alle transazioni create
    firefly_apply_rules: bool = True

    data_dir: Path = Path("/data")
    static_dir: Path = Path("/app/static")

    telegram_bot_token: str = ""
    # Lista di chat id separati da virgola autorizzati a usare il bot
    telegram_allowed_chat_ids: str = ""
    # URL della web app, usato nei link inviati dal bot
    public_url: str = ""

    tz: str = "Europe/Rome"

    @property
    def allowed_chat_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.telegram_allowed_chat_ids.split(","):
            part = part.strip()
            if part:
                out.add(int(part))
        return out

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ingest.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
