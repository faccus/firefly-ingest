"""Formati reali (struttura di Intesa Sanpaolo e BBVA, dati inventati)."""

from datetime import date, datetime
from decimal import Decimal

import openpyxl

from app.history import HistoryIndex, HistTx, tokens
from app.rules import apply_rules
from app.tabular import detect_profile, distinct_values, extract_records, guess_header_row, guess_mapping, headers_at, read_table

D = Decimal


def xlsx(tmp_path, rows, name="f.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    p = tmp_path / name
    wb.save(p)
    return read_table(p.read_bytes(), name)


def isp(tmp_path):
    return xlsx(
        tmp_path,
        [
            [], [], [],
            [None, "Conti e Carte:", "Conto 1000/00001234"],
            [None, "", "CLASSIC CARD MASTERCARD ****1111"],
            [],
            [None, "Data inizio periodo:", "01/10/2026"],
            [],
            ["Data", "Operazione", "Dettagli", "Conto o carta", "Contabilizzazione", "Categoria ", "Valuta", "Importo"],
            [datetime(2026, 10, 31), "Trasferimento Denaro BANCOMAT Pay", "Verso MARIO ROSSI Data 31.10 IBAN Benef. IT00X", "Conto 1000/00001234", "CONTABILIZZATO", "Addebiti vari", "EUR", -8.66],
            [datetime(2026, 10, 30), "Stipendio O Pensione", "Bonifico A Vostro Favore Disposto Da ACME SRL", "Conto 1000/00001234", "CONTABILIZZATO", "Stipendi e pensioni", "EUR", 2100.0],
            [datetime(2026, 10, 24), "Anthropic* Claude Sub", "Anthropic* Claude Sub Dublin 4", "CLASSIC CARD MASTERCARD ****539800001111", "CONTABILIZZATO", "Hi-tech e informatica", "EUR", -21.96],
            [datetime(2026, 10, 25), "Esselunga", "Esselunga Milano", "CLASSIC CARD MASTERCARD ****539800001111", "NON CONTABILIZZATO", "Spesa", "EUR", -40.0],
            [datetime(2026, 10, 15), "Addebito Saldo E/c Carta Di Credito", "Carta N. 5398 Xxxx Xxxx Xx11", "Conto 1000/00001234", "CONTABILIZZATO", "Addebito mia carta di credito  ", "EUR", -236.22],
        ],
    )


def test_isp_guess_and_account_split(tmp_path):
    t = isp(tmp_path)
    idx = guess_header_row(t.rows)
    headers = headers_at(t.rows, idx)
    g = guess_mapping(headers)
    assert g["date"] == "Data" and g["amount"] == "Importo"
    assert g["description"] == ["Operazione"] and g["notes"] == "Dettagli"
    assert g["account"] == "Conto o carta" and g["status"] == "Contabilizzazione" and g["category"] == "Categoria"
    dv = distinct_values(t, idx)
    assert dv["Conto o carta"] == ["CLASSIC CARD MASTERCARD ****539800001111", "Conto 1000/00001234"]
    assert dv["Contabilizzazione"] == ["CONTABILIZZATO", "NON CONTABILIZZATO"]

    profile = {
        "id": 1,
        "name": "ISP",
        "columns": g,
        "options": {
            "account_map": {"Conto 1000/00001234": "10", "CLASSIC CARD MASTERCARD ****539800001111": "11"},
            "status_skip": ["NON CONTABILIZZATO"],
        },
    }
    recs = extract_records(t, profile)
    assert len(recs) == 4  # il non contabilizzato è escluso
    assert [r.account_id for r in recs] == ["10", "10", "11", "10"]
    assert recs[2].description == "Anthropic* Claude Sub" and recs[2].bank_category == "Hi-tech e informatica"


def test_regex_rule_extracts_counterparty(tmp_path):
    recs = extract_records(isp(tmp_path), {"id": 1, "name": "x", "columns": guess_mapping(headers_at(isp(tmp_path).rows, 8))})
    row = {"description": recs[0].description, "counterparty": None, "notes": recs[0].notes, "amount": str(recs[0].amount)}
    rules = [{"id": 1, "match_type": "regex", "field": "notes", "pattern": r"^(?:Verso|Da) (?P<nome>.+?) Data", "set_counterparty": "{nome}", "set_tags": ["bancomat-pay"], "active": 1}]
    changes, rid = apply_rules(rules, row)
    assert rid == 1 and changes == {"counterparty": "Mario Rossi", "tags": ["bancomat-pay"]}


def test_bbva_account_and_card_profiles(tmp_path):
    conto = xlsx(
        tmp_path,
        [
            [],
            [None, None, None, "Ultime transazioni"],
            [],
            [None, "Data valuta", "Data", "Parola chiave", "Movimento", "Importo", "Valuta", "Disponibile", "Valuta", "Osservazioni"],
            [None, "29/10/2026", "29/10/2026", "Bonifico ricevuto", "Regalo", 750, "EUR", 1000.5, "EUR", "Regalo"],
            [None, "05/10/2026", "05/10/2026", "Addebito mensile della carta", "", -2179.8, "EUR", 250.5, "EUR", "5364000000000000"],
        ],
        "conto.xlsx",
    )
    carta = xlsx(
        tmp_path,
        [
            [],
            [None, None, None, "Elenco delle transazioni"],
            [],
            [None, "Data", "Carta", "Causale", "Importo", "Valuta"],
            [None, "17/10/2026", "5364000000000000", "N giro&#39; caffe&#39;", -2.9, "€"],
        ],
        "carta.xlsx",
    )
    gc = guess_mapping(headers_at(conto.rows, guess_header_row(conto.rows)))
    assert gc["date"] == "Data" and gc["description"] == ["Parola chiave", "Movimento"] and gc["notes"] == "Osservazioni"
    gk = guess_mapping(headers_at(carta.rows, guess_header_row(carta.rows)))
    assert gk["description"] == ["Causale"]
    p_conto = {"id": 1, "name": "BBVA conto", "columns": gc}
    p_carta = {"id": 2, "name": "BBVA carta", "columns": gk}
    assert detect_profile(conto.rows, [p_conto, p_carta]) is p_conto
    assert detect_profile(carta.rows, [p_conto, p_carta]) is p_carta
    rc = extract_records(conto, p_conto)
    assert rc[0].description == "Bonifico ricevuto - Regalo" and rc[0].date == date(2026, 10, 29)
    assert rc[1].description == "Addebito mensile della carta" and rc[1].amount == D("-2179.80")
    rk = extract_records(carta, p_carta)
    assert rk[0].description == "N giro' caffe'"


def test_history_suggestions():
    hist = [
        HistTx("Abbonamenti", "anthropic", tokens("Anthropic* Claude Sub")),
        HistTx("Abbonamenti", None, tokens("Anthropic Claude Sub Dublin")),
        HistTx("Spesa", "esselunga", tokens("Esselunga Milano")),
        HistTx("Spesa", "esselunga", tokens("Esselunga")),
        HistTx("Regali", "esselunga", tokens("Esselunga buono")),
        HistTx("Trasporti", None, tokens("Paypal *aziendatras")),
        HistTx("Giochi", None, tokens("Paypal *steam games")),
    ]
    idx = HistoryIndex(hist)
    assert idx.suggest("Anthropic* Claude Sub", None)[0] == "Abbonamenti"
    assert idx.suggest("qualcosa", "Esselunga")[0] == "Spesa"  # 2/3 stessa controparte
    assert idx.suggest("Paypal *steam games", None)[0] == "Giochi"
    assert idx.suggest("Ferramenta Rossi", None) is None
