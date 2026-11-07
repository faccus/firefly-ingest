from datetime import date
from decimal import Decimal

import pytest

from app.quicktext import QuickTextError, parse_line
from app.rules import apply_rules
from app.tabular import detect_profile, extract_records, guess_header_row, guess_mapping, headers_at, read_table
from app.values import parse_amount, parse_date

D = Decimal


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234,56", D("1234.56")),
        ("-12,50", D("-12.50")),
        ("12,5", D("12.50")),
        ("12.50", D("12.50")),
        ("1,234.56", D("1234.56")),
        ("1.234", D("1234.00")),
        ("€ -3,00", D("-3.00")),
        ("(4.20)", D("-4.20")),
        ("15,00-", D("-15.00")),
        ("+100", D("100.00")),
        (12.3, D("12.30")),
        ("", None),
        ("abc", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_date():
    assert parse_date("03/11/2026") == date(2026, 11, 3)
    assert parse_date("2026-11-03") == date(2026, 11, 3)
    assert parse_date("03.11.26") == date(2026, 11, 3)
    assert parse_date("03/11/2026 10:30") == date(2026, 11, 3)
    assert parse_date("Saldo finale") is None


CSV_BANCA = """Estratto conto;;;;
Intestatario: Mario Rossi;;;;
;;;;
Data contabile;Data valuta;Descrizione;Uscite;Entrate
03/11/2026;03/11/2026;PAGAMENTO POS ESSELUNGA MILANO;45,30;
04/11/2026;04/11/2026;BONIFICO DA ACME SRL STIPENDIO;;1.850,00
04/11/2026;04/11/2026;PAGAMENTO POS ESSELUNGA MILANO;45,30;
;;Saldo finale;;2.000,00
""".encode("cp1252")


def test_csv_with_preamble_and_debit_credit():
    t = read_table(CSV_BANCA, "movimenti.csv")
    assert t.delimiter == ";"
    idx = guess_header_row(t.rows)
    headers = headers_at(t.rows, idx)
    assert headers[0] == "Data contabile"
    g = guess_mapping(headers)
    assert g["date"] == "Data contabile"
    assert g["debit"] == "Uscite" and g["credit"] == "Entrate"
    assert g["description"] == ["Descrizione"]
    profile = {"id": 1, "name": "Banca", "columns": g}
    assert detect_profile(t.rows, [profile]) is profile
    recs = extract_records(t, profile)
    assert [r.amount for r in recs] == [D("-45.30"), D("1850.00"), D("-45.30")]
    assert recs[1].description == "BONIFICO DA ACME SRL STIPENDIO"


def test_xlsx(tmp_path):
    import openpyxl
    from datetime import datetime

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Lista movimenti"])
    ws.append([])
    ws.append(["Data", "Operazione", "Dettagli", "Importo"])
    ws.append([datetime(2026, 11, 1), "Pagamento", "AMAZON EU", -19.99])
    ws.append([datetime(2026, 11, 2), "Accredito", "RIMBORSO", 5])
    p = tmp_path / "x.xlsx"
    wb.save(p)
    t = read_table(p.read_bytes(), "x.xlsx")
    idx = guess_header_row(t.rows)
    assert headers_at(t.rows, idx)[3] == "Importo"
    profile = {"id": 1, "name": "X", "columns": {"date": "Data", "amount": "Importo", "description": ["Operazione", "Dettagli"]}}
    recs = extract_records(t, profile)
    assert recs[0].amount == D("-19.99") and recs[0].description == "Pagamento - AMAZON EU"
    assert recs[1].date == date(2026, 11, 2)


def test_quicktext():
    today = date(2026, 11, 7)  # sabato
    e = parse_line("12.50 pizza ieri @carta #cene !amici", today)
    assert e.amount == D("-12.50") and e.date == date(2026, 11, 6)
    assert e.description == "Pizza" and e.account == "carta" and e.category == "cene" and e.tags == ["amici"]
    e = parse_line("+1800 stipendio 27/10", today)
    assert e.amount == D("1800.00") and e.date == date(2026, 10, 27)
    e = parse_line("caffè 1,20 lunedì", today)
    assert e.amount == D("-1.20") and e.date == date(2026, 11, 2)
    e = parse_line("regalo 30 25/12", today)
    assert e.date == date(2025, 12, 25)  # data futura senza anno -> anno scorso
    with pytest.raises(QuickTextError):
        parse_line("solo testo", today)


def test_rules_priority_and_fill():
    rules = [
        {"id": 1, "priority": 10, "pattern": "esselunga", "set_category": "Spesa", "set_counterparty": "Esselunga", "active": 1},
        {"id": 2, "priority": 50, "pattern": "pos", "set_category": "Varie", "set_budget": "Quotidiano", "active": 1},
        {"id": 3, "priority": 5, "pattern": "esselunga", "direction": "in", "set_category": "Rimborsi", "active": 1},
    ]
    row = {"description": "PAGAMENTO POS ESSELUNGA", "counterparty": None, "amount": "-45.30", "tags": []}
    changes, first = apply_rules(rules, row)
    assert first == 1
    assert changes == {"category": "Spesa", "counterparty": "Esselunga", "budget": "Quotidiano"}
