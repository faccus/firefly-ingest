"""Flusso completo via API con un Firefly finto (respx)."""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import firefly
from app.main import app

from .test_parsing import CSV_BANCA

FF = "http://firefly.test/api/v1"


def page(data):
    return {"data": data, "meta": {"pagination": {"current_page": 1, "total_pages": 1}}}


@pytest.fixture
def ff_mock():
    firefly.set_firefly(None)
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{FF}/accounts").respond(
            json=page([{"id": "1", "attributes": {"name": "Conto Corrente", "iban": None, "currency_code": "EUR"}},
                       {"id": "2", "attributes": {"name": "Carta", "iban": None, "currency_code": "EUR"}}])
        )
        m.get(f"{FF}/categories").respond(json=page([{"id": "1", "attributes": {"name": "Spesa"}}, {"id": "2", "attributes": {"name": "Cene"}}]))
        m.get(f"{FF}/budgets").respond(json=page([]))
        m.get(f"{FF}/tags").respond(json=page([]))
        # In Firefly c'è già lo stipendio del 04/11 (inserito a mano)
        m.get(f"{FF}/accounts/1/transactions").respond(
            json=page([{"id": "99", "attributes": {"transactions": [
                {"date": "2026-11-04T00:00:00+01:00", "amount": "1850.00", "type": "deposit", "source_id": "50",
                 "destination_id": "1", "description": "Stipendio", "external_id": None}]}}])
        )
        created = []

        def create(request: httpx.Request):
            body = json.loads(request.content)
            created.append(body)
            return httpx.Response(200, json={"data": {"id": str(100 + len(created))}})

        m.post(f"{FF}/transactions").mock(side_effect=create)
        m.created = created  # type: ignore[attr-defined]
        yield m
    firefly.set_firefly(None)


def test_full_flow(ff_mock):
    with TestClient(app) as c:
        # 1. formato sconosciuto -> serve un profilo
        r = c.post("/api/upload", files={"file": ("mov.csv", CSV_BANCA, "text/csv")})
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["needs_profile"]
        up = res["upload_id"]
        insp = c.get(f"/api/uploads/{up}/inspect").json()
        assert insp["headers"][0] == "Data contabile"

        # 2. crea profilo dal mapping proposto e processa
        prof = c.post("/api/profiles", json={"name": "Banca Test", "account_id": "1", "columns": insp["guess"]}).json()
        c.post("/api/rules", json={"pattern": "esselunga", "set_category": "Spesa", "set_counterparty": "Esselunga"})
        r = c.post(f"/api/uploads/{up}/process", json={"profile_id": prof["id"]})
        assert r.status_code == 200, r.text
        bid = r.json()["batch_id"]

        b = c.get(f"/api/batches/{bid}").json()
        statuses = [row["status"] for row in b["rows"]]
        assert statuses == ["new", "possible_duplicate", "new"]
        assert b["rows"][0]["category"] == "Spesa" and b["rows"][0]["counterparty"] == "Esselunga"

        # 3. import di default: solo le righe pronte
        r = c.post(f"/api/batches/{bid}/import", json={})
        assert r.json() == {"imported": 2, "duplicate": 0, "error": 0}
        split = ff_mock.created[0]["transactions"][0]
        assert split["type"] == "withdrawal" and split["source_id"] == "1"
        assert split["destination_name"] == "Esselunga" and split["amount"] == "45.30"
        assert split["category_name"] == "Spesa" and split["external_id"].startswith("ffi-")

        # 4. ricaricando lo stesso file le righe già importate risultano duplicate
        r = c.post("/api/upload", files={"file": ("mov.csv", CSV_BANCA, "text/csv")})
        b2 = c.get(f"/api/batches/{r.json()['batch_id']}").json()
        assert [row["status"] for row in b2["rows"]] == ["duplicate", "possible_duplicate", "duplicate"]


def test_quick_text(ff_mock):
    with TestClient(app) as c:
        c.post("/api/aliases", json={"kind": "account", "alias": "carta", "value": "Carta"})
        r = c.post("/api/text", json={"text": "12.50 pizza ieri @carta #cene\n3 caffè"})
        assert r.status_code == 422  # seconda riga senza conto e nessun predefinito
        c.put("/api/settings", json={"default_account_id": "1"})
        r = c.post("/api/text", json={"text": "12.50 pizza ieri @carta #cene\n3 caffè"})
        assert r.status_code == 200, r.text
        rows = c.get(f"/api/batches/{r.json()['batch_id']}").json()["rows"]
        assert rows[0]["account_id"] == "2" and rows[0]["category"] == "Cene"
        assert rows[1]["account_id"] == "1" and rows[1]["amount"] == "-3.00"
