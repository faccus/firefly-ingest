"""Parsing di importi e date nei formati tipici delle banche italiane."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

DATE_FORMATS = [
    "%d/%m/%Y",
    "%d/%m/%y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
]

_CURRENCY = re.compile(r"(EUR|€|\s)", re.IGNORECASE)


def parse_amount(value: Any, decimal_sep: str | None = None) -> Decimal | None:
    """Converte "1.234,56", "-12,50 €", "(3.00)", 12.5 ... in Decimal.

    decimal_sep: "," o "." per forzare; None = rilevamento automatico.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    s = str(value).strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = _CURRENCY.sub("", s)
    if s.endswith("-"):
        negative, s = True, s[:-1]
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        negative, s = not negative, s[1:]
    if not s or not re.fullmatch(r"[\d.,']+", s):
        return None
    s = s.replace("'", "")
    sep = decimal_sep
    if sep is None:
        last_comma, last_dot = s.rfind(","), s.rfind(".")
        if last_comma >= 0 and last_dot >= 0:
            # Entrambi presenti: l'ultimo è il separatore decimale
            sep = "," if last_comma > last_dot else "."
        elif last_comma >= 0:
            # Solo virgole: più virgole = migliaia ("1,234,567"), altrimenti decimale (uso italiano)
            sep = "." if s.count(",") > 1 else ","
        elif last_dot >= 0:
            # Solo punti: più punti o esattamente 3 cifre finali = migliaia ("1.234"), altrimenti decimale
            tail = s[last_dot + 1 :]
            sep = "," if (s.count(".") > 1 or len(tail) == 3) else "."
        else:
            sep = "."
    thousands = "." if sep == "," else ","
    s = s.replace(thousands, "")
    if sep == ",":
        s = s.replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    d = d.quantize(Decimal("0.01"))
    return -d if negative else d


def parse_date(value: Any, fmt: str | None = None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
        # Seriale Excel
        from datetime import timedelta

        return date(1899, 12, 30) + timedelta(days=int(value))
    s = str(value).strip()
    if not s:
        return None
    formats = [fmt] if fmt else DATE_FORMATS
    for f in formats:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    # Tentativo sulla sola parte iniziale (es. "01/02/2026 - 10:30")
    m = re.match(r"(\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4})", s)
    if m and m.group(1) != s:
        return parse_date(m.group(1), fmt=None)
    return None


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_for_hash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())
