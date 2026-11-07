"""Parser per l'inserimento rapido: "12.50 pizza ieri @carta #cene".

Sintassi (ordine libero, una transazione per riga):
  - importo: 12.50 / 12,50 / €12 ; con "+" davanti è un'entrata, altrimenti un'uscita
  - data: oggi, ieri, altroieri, lunedì..domenica (ultimo passato), 3/11, 03/11/2026
  - conto: @alias  (alias configurati, oppure nome del conto Firefly)
  - categoria: #alias o #NomeCategoria
  - tag: !tag
  - tutto il resto è la descrizione
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .values import parse_amount

WEEKDAYS = {
    "lunedi": 0, "lunedì": 0, "martedi": 1, "martedì": 1, "mercoledi": 2, "mercoledì": 2,
    "giovedi": 3, "giovedì": 3, "venerdi": 4, "venerdì": 4, "sabato": 5, "domenica": 6,
}
AMOUNT_RE = re.compile(r"^[+-]?(€|eur)?\d+([.,]\d{1,2})?(€|eur)?$", re.IGNORECASE)
DATE_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?$")


@dataclass
class QuickEntry:
    amount: Decimal
    date: date
    description: str
    account: str | None = None  # alias o nome grezzo, da risolvere
    category: str | None = None
    tags: list[str] = field(default_factory=list)


class QuickTextError(ValueError):
    pass


def _parse_date_token(tok: str, today: date) -> date | None:
    t = tok.lower().strip(",")
    if t == "oggi":
        return today
    if t == "ieri":
        return today - timedelta(days=1)
    if t in ("altroieri", "l'altroieri"):
        return today - timedelta(days=2)
    if t in WEEKDAYS:
        delta = (today.weekday() - WEEKDAYS[t]) % 7 or 7
        return today - timedelta(days=delta)
    m = DATE_RE.match(t)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = today.year if y is None else (2000 + int(y) if len(y) == 2 else int(y))
        try:
            res = date(year, mth, d)
        except ValueError:
            return None
        if y is None and res > today:
            res = date(year - 1, mth, d)
        return res
    return None


def parse_line(line: str, today: date) -> QuickEntry:
    tokens = line.split()
    amount: Decimal | None = None
    income = False
    when: date | None = None
    account = category = None
    tags: list[str] = []
    words: list[str] = []
    for tok in tokens:
        if amount is None and AMOUNT_RE.match(tok):
            income = tok.startswith("+")
            amount = parse_amount(tok.lstrip("+-"), ",") if "," in tok else parse_amount(tok.lstrip("+-"), ".")
            continue
        if tok.startswith("@") and len(tok) > 1:
            account = tok[1:]
            continue
        if tok.startswith("#") and len(tok) > 1:
            category = tok[1:]
            continue
        if tok.startswith("!") and len(tok) > 1:
            tags.append(tok[1:])
            continue
        if when is None:
            d = _parse_date_token(tok, today)
            if d is not None:
                when = d
                continue
        words.append(tok)
    if amount is None:
        raise QuickTextError(f"Importo non trovato in: {line!r}")
    description = " ".join(words).strip() or (category or "Spesa")
    return QuickEntry(
        amount=abs(amount) if income else -abs(amount),
        date=when or today,
        description=description[:1].upper() + description[1:],
        account=account,
        category=category,
        tags=tags,
    )


def parse_text(text: str, today: date) -> list[QuickEntry]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise QuickTextError("Testo vuoto")
    return [parse_line(ln, today) for ln in lines]
