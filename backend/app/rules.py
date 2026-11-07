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
    return match(rule, row) is not None


GROUP_REF = re.compile(r"\{(\w+)\}")


def _expand(template: str, m: "re.Match[str] | None") -> str:
    """Sostituisce {1} / {nome} con i gruppi della regex; "{1}" senza gruppo resta com'è."""
    if m is None or not m.groups():
        return template

    def sub(g: "re.Match[str]") -> str:
        key = g.group(1)
        try:
            val = m.group(int(key)) if key.isdigit() else m.group(key)
        except IndexError:
            return g.group(0)
        val = (val or "").strip()
        # "FABIO GALLOTTI" -> "Fabio Gallotti"
        return val.title() if val.isupper() else val

    return GROUP_REF.sub(sub, template).strip()



def match(rule: dict[str, Any], row: dict[str, Any]) -> "re.Match[str] | bool | None":
    """None se la regola non corrisponde; il Match per le regex, True altrimenti."""
    if not rule.get("active", 1):
        return None
    amount = Decimal(str(row["amount"]))
    direction = rule.get("direction") or "any"
    if direction == "out" and amount >= 0:
        return None
    if direction == "in" and amount <= 0:
        return None
    if rule.get("account_id") and str(rule["account_id"]) != str(row.get("account_id")):
        return None
    a = abs(amount)
    if rule.get("amount_min") is not None and a < Decimal(str(rule["amount_min"])):
        return None
    if rule.get("amount_max") is not None and a > Decimal(str(rule["amount_max"])):
        return None
    text = _text_for(rule, row)
    pattern = rule.get("pattern") or ""
    match rule.get("match_type") or "contains":
        case "regex":
            try:
                return re.search(pattern, text, re.IGNORECASE)
            except re.error:
                return None
        case "equals":
            return True if text.strip().casefold() == pattern.strip().casefold() else None
        case "starts":
            return True if text.strip().casefold().startswith(pattern.strip().casefold()) else None
        case _:
            return True if pattern.casefold() in text.casefold() else None


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
        m = match(rule, row)
        if m is None:
            continue
        rx = m if isinstance(m, re.Match) else None
        changed = False
        if rule.get("set_skip") and "skip" not in out:
            out["skip"] = True
            changed = True
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
            if dst == "tags":
                out[dst] = [_expand(t, rx) for t in val]
            elif dst in ("counterparty", "description", "category"):
                expanded = _expand(val, rx)
                if not expanded:
                    continue
                out[dst] = expanded
            else:
                out[dst] = val
            changed = True
        if changed and first is None:
            first = rule["id"]
    return out, first
