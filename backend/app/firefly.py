"""Client minimale per l'API di Firefly III (v1)."""

import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from .config import get_settings


class FireflyError(Exception):
    def __init__(self, message: str, status: int | None = None, duplicate: bool = False):
        super().__init__(message)
        self.status = status
        self.duplicate = duplicate


@dataclass
class ExistingTx:
    date: str  # YYYY-MM-DD
    amount: Decimal  # con segno, relativo al conto interrogato
    description: str
    external_id: str | None
    group_id: str


class FireflyClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        s = get_settings()
        self.base_url = (base_url or s.firefly_url).rstrip("/")
        self.token = token if token is not None else s.firefly_token
        self._transport = transport
        self._meta_cache: tuple[float, dict[str, Any]] | None = None

    def _client(self) -> httpx.AsyncClient:
        if not self.token:
            raise FireflyError("FIREFLY_TOKEN non configurato")
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/json",
            },
            timeout=30,
            transport=self._transport,
        )

    async def _get_all(self, client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            r = await client.get(path, params={**(params or {}), "page": page, "limit": 200})
            if r.status_code >= 400:
                raise FireflyError(f"GET {path}: HTTP {r.status_code} {r.text[:200]}", r.status_code)
            body = r.json()
            out.extend(body.get("data", []))
            pag = body.get("meta", {}).get("pagination", {})
            if not pag or pag.get("current_page", 1) >= pag.get("total_pages", 1):
                return out
            page += 1

    async def about(self) -> dict[str, Any]:
        async with self._client() as c:
            r = await c.get("/about")
            if r.status_code >= 400:
                raise FireflyError(f"HTTP {r.status_code}", r.status_code)
            return r.json().get("data", {})

    async def metadata(self, force: bool = False) -> dict[str, Any]:
        """Conti asset, categorie, budget e tag (cache 5 minuti)."""
        if not force and self._meta_cache and time.monotonic() - self._meta_cache[0] < 300:
            return self._meta_cache[1]
        async with self._client() as c:
            accounts = await self._get_all(c, "/accounts", {"type": "asset"})
            categories = await self._get_all(c, "/categories")
            budgets = await self._get_all(c, "/budgets")
            tags = await self._get_all(c, "/tags")
        meta = {
            "accounts": [
                {
                    "id": a["id"],
                    "name": a["attributes"]["name"],
                    "iban": a["attributes"].get("iban"),
                    "currency": a["attributes"].get("currency_code"),
                    "active": a["attributes"].get("active", True),
                }
                for a in accounts
            ],
            "categories": sorted(c["attributes"]["name"] for c in categories),
            "budgets": sorted(b["attributes"]["name"] for b in budgets if b["attributes"].get("active", True)),
            "tags": sorted(t["attributes"]["tag"] for t in tags),
        }
        self._meta_cache = (time.monotonic(), meta)
        return meta

    def invalidate(self) -> None:
        self._meta_cache = None

    async def account_transactions(self, account_id: str, start: date, end: date) -> list[ExistingTx]:
        async with self._client() as c:
            groups = await self._get_all(
                c, f"/accounts/{account_id}/transactions", {"start": start.isoformat(), "end": end.isoformat()}
            )
        out: list[ExistingTx] = []
        for g in groups:
            for t in g["attributes"]["transactions"]:
                amount = Decimal(str(t["amount"]))
                if str(t.get("source_id")) == str(account_id):
                    amount = -amount
                elif str(t.get("destination_id")) != str(account_id):
                    continue
                out.append(
                    ExistingTx(
                        date=str(t["date"])[:10],
                        amount=amount,
                        description=t.get("description") or "",
                        external_id=t.get("external_id"),
                        group_id=str(g["id"]),
                    )
                )
        return out

    async def create_transaction(self, split: dict[str, Any], apply_rules: bool | None = None) -> str:
        if apply_rules is None:
            apply_rules = get_settings().firefly_apply_rules
        payload = {
            "error_if_duplicate_hash": True,
            "apply_rules": apply_rules,
            "fire_webhooks": True,
            "transactions": [split],
        }
        async with self._client() as c:
            r = await c.post("/transactions", json=payload)
        if r.status_code in (200, 201):
            return str(r.json()["data"]["id"])
        text = r.text
        dup = r.status_code == 422 and "uplicate" in text
        try:
            body = r.json()
            msg = body.get("message") or text
            errors = body.get("errors")
            if errors:
                msg = f"{msg} {errors}"
        except ValueError:
            msg = text
        raise FireflyError(str(msg)[:500], r.status_code, duplicate=dup)


_client: FireflyClient | None = None


def get_firefly() -> FireflyClient:
    global _client
    if _client is None:
        _client = FireflyClient()
    return _client


def set_firefly(client: FireflyClient | None) -> None:
    """Usato nei test per iniettare un client finto."""
    global _client
    _client = client
