"""Motore di regole locali: assegna categoria, budget, tag, controparte o giroconto."""

import re
from decimal import Decimal
from typing import Any

SET_FIELDS = {
    "set_category": "category",
    "set_budget": "budget",
    "set_tags": "tags",
    "set_counterparty": "counterparty",
    "set_description": "description",
    "set_transfer_account_id": "transfer_account_id",
}


def _text_for(rule: dict[str, Any], row: dict[str, Any]) -> str:
    desc = row.get("description") or ""
    cp = row.get("counterparty") or ""
    notes = row.get("notes") or ""
    match rule.get("field") or "any":
        case "description":
            return desc
        case "counterparty":
            return cp
        case "notes":
            return notes
        case _:
            return f"{desc} {cp} {notes}"


def matches(rule: dict[str, Any], row: dict[str, Any]) -> bool:
    if not rule.get("active", 1):
        return False
    amount = Decimal(str(row["amount"]))
    direction = rule.get("direction") or "any"
    if direction == "out" and amount >= 0:
        return False
    if direction == "in" and amount <= 0:
        return False
    if rule.get("account_id") and str(rule["account_id"]) != str(row.get("account_id")):
        return False
    a = abs(amount)
    if rule.get("amount_min") is not None and a < Decimal(str(rule["amount_min"])):
        return False
    if rule.get("amount_max") is not None and a > Decimal(str(rule["amount_max"])):
        return False
    text = _text_for(rule, row)
    pattern = rule.get("pattern") or ""
    match rule.get("match_type") or "contains":
        case "regex":
            try:
                return re.search(pattern, text, re.IGNORECASE) is not None
            except re.error:
                return False
        case "equals":
            return text.strip().casefold() == pattern.strip().casefold()
        case "starts":
            return text.strip().casefold().startswith(pattern.strip().casefold())
        case _:
            return pattern.casefold() in text.casefold()


def sort_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rules, key=lambda r: (r.get("priority") or 100, r["id"]))


def apply_rules(rules: list[dict[str, Any]], row: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    """Applica le regole in ordine di priorità.

    Ogni regola che corrisponde riempie solo i campi ancora vuoti, quindi le regole
    più specifiche (priorità più bassa) vincono e quelle generiche completano il resto.
    Ritorna (campi da impostare, id della prima regola che ha corrisposto).
    """
    out: dict[str, Any] = {}
    first: int | None = None
    for rule in sort_rules(rules):
        if not matches(rule, row):
            continue
        changed = False
        for src, dst in SET_FIELDS.items():
            val = rule.get(src)
            if val in (None, "", []):
                continue
            current = out.get(dst, row.get(dst))
            # descrizione e controparte sono sempre presenti: le sovrascrive solo la prima regola
            if dst in ("description", "counterparty"):
                if dst in out:
                    continue
            elif current not in (None, "", []):
                continue
            out[dst] = list(val) if dst == "tags" else val
            changed = True
        if changed and first is None:
            first = rule["id"]
    return out, first
