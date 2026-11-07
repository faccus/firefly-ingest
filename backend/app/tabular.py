"""Lettura di CSV/XLSX/XLS bancari ed estrazione dei movimenti tramite profilo."""

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from .values import clean_text, parse_amount, parse_date

MAX_HEADER_SCAN = 40

FIELD_KEYWORDS: dict[str, list[str]] = {
    "date": ["data contabile", "data operazione", "data", "date", "booking date"],
    "value_date": ["data valuta", "valuta", "value date"],
    "amount": ["importo", "amount", "ammontare"],
    "debit": ["uscite", "addebit", "dare", "debit", "uscita"],
    "credit": ["entrate", "accredit", "avere", "credit", "entrata"],
    "description": ["descrizione", "causale", "dettagli", "description", "operazione", "concetto"],
    "counterparty": ["beneficiario", "esercente", "controparte", "ordinante", "merchant", "payee"],
    "notes": ["note", "notes", "riferimento"],
}


class TableError(Exception):
    pass


@dataclass
class Table:
    rows: list[list[Any]]
    encoding: str | None = None
    delimiter: str | None = None


@dataclass
class Record:
    date: date
    amount: Decimal
    description: str
    counterparty: str | None = None
    notes: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _norm(s: Any) -> str:
    return clean_text(s).lower()


def read_table(content: bytes, filename: str, encoding: str | None = None, delimiter: str | None = None) -> Table:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")) or content[:2] == b"PK":
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001
            raise TableError(f"File Excel non leggibile: {e}") from e
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
        return Table(rows=rows)
    if name.endswith(".xls") or content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        import xlrd

        try:
            book = xlrd.open_workbook(file_contents=content)
        except Exception as e:  # noqa: BLE001
            raise TableError(f"File XLS non leggibile: {e}") from e
        sh = book.sheet_by_index(0)
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                else:
                    row.append(cell.value)
            rows.append(row)
        return Table(rows=rows)

    text = None
    used_encoding = encoding
    for enc in [encoding] if encoding else ["utf-8-sig", "cp1252", "latin-1"]:
        try:
            text = content.decode(enc)
            used_encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise TableError("Codifica del file non riconosciuta")
    if not delimiter:
        sample = "\n".join(text.splitlines()[:50])
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
        except csv.Error:
            counts = {d: sample.count(d) for d in ";,\t|"}
            delimiter = max(counts, key=counts.get)  # type: ignore[arg-type]
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return Table(rows=rows, encoding=used_encoding, delimiter=delimiter)


def _non_empty(row: list[Any]) -> list[Any]:
    return [c for c in row if c is not None and str(c).strip() != ""]


def guess_header_row(rows: list[list[Any]]) -> int:
    """Indice della riga di intestazione: la prima con >=3 celle testuali
    seguita da una riga che contiene almeno una data."""
    best, best_score = 0, -1
    for i, row in enumerate(rows[:MAX_HEADER_SCAN]):
        cells = _non_empty(row)
        texts = [c for c in cells if isinstance(c, str) and parse_amount(c) is None and parse_date(c) is None]
        if len(texts) < 2:
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else []
        has_date_below = any(parse_date(c) is not None for c in _non_empty(nxt))
        keyword_hits = sum(1 for c in texts for kws in FIELD_KEYWORDS.values() if any(k in _norm(c) for k in kws))
        score = len(texts) + 3 * keyword_hits + (10 if has_date_below else 0)
        if score > best_score:
            best, best_score = i, score
    return best


def find_header_by_columns(rows: list[list[Any]], columns: list[str]) -> int | None:
    wanted = {_norm(c) for c in columns if c}
    if not wanted:
        return None
    for i, row in enumerate(rows[:MAX_HEADER_SCAN]):
        present = {_norm(c) for c in row}
        if wanted <= present:
            return i
    return None


def headers_at(rows: list[list[Any]], idx: int) -> list[str]:
    return [clean_text(c) for c in rows[idx]] if idx < len(rows) else []


def guess_mapping(headers: list[str]) -> dict[str, Any]:
    """Prima ipotesi di mapping colonne -> campi, a partire dai nomi."""
    mapping: dict[str, Any] = {k: None for k in FIELD_KEYWORDS}
    used: set[str] = set()
    for fld in ["value_date", "date", "debit", "credit", "amount", "counterparty", "notes", "description"]:
        for kw in FIELD_KEYWORDS[fld]:
            hit = next((h for h in headers if h and h not in used and kw in h.lower()), None)
            if hit:
                mapping[fld] = hit
                used.add(hit)
                break
    mapping["description"] = [mapping["description"]] if mapping["description"] else []
    if mapping["amount"]:
        mapping["debit"] = mapping["credit"] = None
    mapping.pop("value_date", None)
    return mapping


def profile_columns(columns: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for v in columns.values():
        if isinstance(v, list):
            out.extend(x for x in v if x)
        elif v:
            out.append(v)
    return out


def detect_profile(rows: list[list[Any]], profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Il profilo con più colonne tutte presenti nell'intestazione."""
    best, best_n = None, 0
    for p in profiles:
        cols = profile_columns(p["columns"])
        if find_header_by_columns(rows, cols) is not None and len(cols) > best_n:
            best, best_n = p, len(cols)
    return best


def extract_records(table: Table, profile: dict[str, Any]) -> list[Record]:
    cols: dict[str, Any] = profile["columns"]
    header_idx = find_header_by_columns(table.rows, profile_columns(cols))
    if header_idx is None:
        raise TableError("Le colonne del profilo non sono presenti nel file")
    headers = [_norm(h) for h in table.rows[header_idx]]

    def col_index(name: str | None) -> int | None:
        if not name:
            return None
        try:
            return headers.index(_norm(name))
        except ValueError:
            return None

    i_date = col_index(cols.get("date"))
    if i_date is None:
        raise TableError("Colonna data non configurata")
    i_amount = col_index(cols.get("amount"))
    i_debit = col_index(cols.get("debit"))
    i_credit = col_index(cols.get("credit"))
    if i_amount is None and i_debit is None and i_credit is None:
        raise TableError("Configura una colonna importo oppure uscite/entrate")
    desc_cols = cols.get("description") or []
    if isinstance(desc_cols, str):
        desc_cols = [desc_cols]
    i_desc = [i for i in (col_index(c) for c in desc_cols) if i is not None]
    i_cp = col_index(cols.get("counterparty"))
    i_notes = col_index(cols.get("notes"))
    dec = profile.get("decimal_sep") or None
    invert = bool(profile.get("invert_sign"))
    raw_headers = [clean_text(h) for h in table.rows[header_idx]]

    def cell(row: list[Any], i: int | None) -> Any:
        if i is None or i >= len(row):
            return None
        return row[i]

    out: list[Record] = []
    for row in table.rows[header_idx + 1 :]:
        if not _non_empty(row):
            continue
        d = parse_date(cell(row, i_date), profile.get("date_format") or None)
        if d is None:
            continue  # righe di totale/saldo/footer
        if i_amount is not None:
            amount = parse_amount(cell(row, i_amount), dec)
        else:
            debit = parse_amount(cell(row, i_debit), dec)
            credit = parse_amount(cell(row, i_credit), dec)
            if debit is None and credit is None:
                amount = None
            else:
                amount = (abs(credit) if credit else Decimal("0")) - (abs(debit) if debit else Decimal("0"))
        if amount is None or amount == 0:
            continue
        if invert:
            amount = -amount
        description = " - ".join(t for t in (clean_text(cell(row, i)) for i in i_desc) if t)
        counterparty = clean_text(cell(row, i_cp)) or None
        notes = clean_text(cell(row, i_notes)) or None
        raw = {raw_headers[j] or f"col{j}": (str(v) if v is not None else None) for j, v in enumerate(row) if j < len(raw_headers)}
        out.append(Record(date=d, amount=amount, description=description or counterparty or "(senza descrizione)", counterparty=counterparty, notes=notes, raw=raw))
    return out
