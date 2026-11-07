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
        m.get(f"{FF}/accounts", params={"type": "expense"}).respond(json=page([{"id": "70", "attributes": {"name": "Pizzeria"}}]))
        m.get(f"{FF}/accounts", params={"type": "revenue"}).respond(json=page([]))
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


def test_transfer_dedupe_and_skip_rule(ff_mock):
    # Sul conto carta (id 2) Firefly ha già il giroconto creato dall'estratto del conto
    ff_mock.get(f"{FF}/accounts/2/transactions").respond(
        json=page([{"id": "77", "attributes": {"transactions": [
            {"date": "2026-11-05T00:00:00+01:00", "amount": "300.00", "type": "transfer", "source_id": "1",
             "destination_id": "2", "description": "Saldo carta", "external_id": "x"}]}}])
    )
    with TestClient(app) as c:
        c.put("/api/settings", json={"default_account_id": "1"})
        c.post("/api/rules", json={"pattern": "saldo carta", "set_transfer_account_id": "2"})
        c.post("/api/rules", json={"pattern": "autorizzazione", "set_skip": True})
        bid = c.post("/api/text", json={"text": "300 saldo carta 5/11\n10 autorizzazione"}).json()["batch_id"]
        rows = c.get(f"/api/batches/{bid}").json()["rows"]
        assert rows[0]["transfer_account_id"] == "2" and rows[0]["status"] == "new"
        assert rows[1]["status"] == "skipped"
        res = c.post(f"/api/batches/{bid}/import", json={}).json()
        assert res == {"imported": 0, "duplicate": 1, "error": 0}
        assert ff_mock.created == []
        rows = c.get(f"/api/batches/{bid}").json()["rows"]
        assert "Giroconto già presente" in rows[0]["dup_reason"]


def test_preview_matches_import(ff_mock):
    with TestClient(app) as c:
        c.put("/api/settings", json={"default_account_id": "1"})
        c.post("/api/rules", json={"pattern": "pizza", "set_counterparty": "Pizzeria"})
        c.post("/api/rules", json={"pattern": "autorizzazione", "set_skip": True})
        bid = c.post("/api/text", json={"text": "12.50 pizza ieri #Cene\n+40 rimborso #Rimborsi !amici\n10 autorizzazione"}).json()["batch_id"]
        rows = c.get(f"/api/batches/{bid}").json()["rows"]
        ids = [r["id"] for r in rows]
        p = c.post(f"/api/batches/{bid}/preview", json={"row_ids": ids}).json()
        a, b, s = p["items"]
        assert (a["type"], a["source"], a["destination"], a["amount"], a["category"]) == ("withdrawal", "Conto Corrente", "Pizzeria", "12.50", "Cene")
        assert (b["type"], b["destination"], b["tags"]) == ("deposit", "Conto Corrente", ["amici"])
        assert s["forced"] and "riga esclusa, inclusa a mano" in s["warnings"] and "senza categoria" in s["warnings"]
        assert p["new_categories"] == ["Rimborsi"] and p["new_tags"] == ["amici"] and p["new_counterparties"] == []
        assert p["summary"]["by_type"] == {"uscita": {"count": 2, "total": "22.50"}, "entrata": {"count": 1, "total": "40.00"}}
        # l'invio usa gli stessi dati: anche la riga esclusa, perché scelta esplicitamente
        assert c.post(f"/api/batches/{bid}/import", json={"row_ids": ids}).json()["imported"] == 3
        sent = [t["transactions"][0] for t in ff_mock.created]
        assert [(x["type"], x["amount"]) for x in sent] == [("withdrawal", "12.50"), ("deposit", "40.00"), ("withdrawal", "10.00")]


def test_reapply_recomputes_but_keeps_manual_edits(ff_mock):
    with TestClient(app) as c:
        c.put("/api/settings", json={"default_account_id": "1"})
        bid = c.post("/api/text", json={"text": "8.66 Verso FABIO GALLOTTI Data 31.10\n5 autorizzazione\n3 caffè bar"}).json()["batch_id"]
        rows = c.get(f"/api/batches/{bid}").json()["rows"]
        assert rows[0]["counterparty"] is None and rows[0]["category"] is None
        # una categoria scelta a mano non va persa
        c.patch(f"/api/rows/{rows[2]['id']}", json={"category": "Bar"})
        # regole create dopo il caricamento
        c.post("/api/rules", json={"match_type": "regex", "pattern": r"Verso (?P<nome>.+?) Data", "set_counterparty": "{nome}", "set_category": "Amici"})
        skip = c.post("/api/rules", json={"pattern": "autorizzazione", "set_skip": True}).json()
        c.post("/api/rules", json={"pattern": "caffè", "set_category": "Colazione"})
        assert c.post(f"/api/batches/{bid}/reapply-rules").json()["changed"] == 2
        rows = c.get(f"/api/batches/{bid}").json()["rows"]
        assert (rows[0]["counterparty"], rows[0]["category"], rows[0]["cat_source"]) == ("Fabio Gallotti", "Amici", "rule")
        assert rows[1]["status"] == "skipped"
        assert (rows[2]["category"], rows[2]["cat_source"]) == ("Bar", "manual")
        # disattivando la regola "ignora" la riga torna pronta
        c.put(f"/api/rules/{skip['id']}", json={"pattern": "autorizzazione", "set_skip": True, "active": False})
        c.post(f"/api/batches/{bid}/reapply-rules")
        assert c.get(f"/api/batches/{bid}").json()["rows"][1]["status"] == "new"
        # svuotando il campo, la categoria torna alle regole
        c.patch(f"/api/rows/{rows[2]['id']}", json={"category": ""})
        c.post(f"/api/batches/{bid}/reapply-rules")
        assert c.get(f"/api/batches/{bid}").json()["rows"][2]["category"] == "Colazione"
