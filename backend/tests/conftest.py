import pytest

from app import db
from app.config import get_settings


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIREFLY_URL", "http://firefly.test")
    monkeypatch.setenv("FIREFLY_TOKEN", "test-token")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "static"))
    get_settings.cache_clear()
    db.init_db()
    yield tmp_path
    get_settings.cache_clear()
