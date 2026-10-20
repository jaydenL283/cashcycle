from datetime import date
from pathlib import Path

from cashcycle.cli import main
from cashcycle.early_warning import build_warnings, detect

LIVE = Path(__file__).resolve().parents[1] / "data" / "live" / "r3con-tech"
AS_OF = date(2026, 10, 20)


def _by_kind(ws):
    out = {}
    for w in ws:
        out.setdefault(w["kind"], []).append(w)
    return out


def test_live_warnings():
    ws = build_warnings(LIVE, AS_OF)
    k = _by_kind(ws)
    # money in: Cormorant pays every 2 months around the 18th; October is 2 days late
    cormorant = next(w for w in k["missing_receipt"] if w["counterparty"] == "Cormorant Systems")
    assert cormorant["days_late"] == 2 and cormorant["expected_amount"] == 4850.0 and cormorant["direction"] == "in"
    # money out: four doubled direct debits in September
    dups = {w["counterparty"] for w in k["duplicate_payment"]}
    assert dups == {"Amazon Web Services", "WeWork", "Figma", "Vercel"}
    assert all(w["severity"] == "high" and len(w["transactions"].split(", ")) == 2 for w in k["duplicate_payment"])
    # Northwind direct debit missing with overdue bills -> high
    nw = next(w for w in k["missing_payment"] if w["counterparty"] == "Northwind Facilities")
    assert nw["severity"] == "high" and nw["days_late"] == 6 and "past due" in nw["detail"]
    # Fathom: bill due today, and Deel appeared in the same category
    fa = next(w for w in k["missing_payment"] if w["counterparty"] == "Fathom Talent Partners")
    assert fa["severity"] == "medium" and "due today" in fa["detail"] and "Deel" in fa["detail"]
    # Clearview: next bill not due yet -> info only
    assert next(w for w in k["missing_payment"] if w["counterparty"] == "Clearview Media")["severity"] == "info"
    # unusual amounts and new payee
    assert {w["counterparty"] for w in k["unusual_amount"]} >= {"Google Cloud", "Amazon Web Services"}
    assert [w["counterparty"] for w in k["new_payee"]] == ["Deel UK Ltd"]
    # October payroll paid on 18 Sep: flagged, and salaries are not expected again on the 25th
    assert k["early_payroll"][0]["counterparty"] == "Payroll"
    upcoming = {w["counterparty"] for w in k.get("upcoming_payment", [])}
    assert "Remote People Ltd" in upcoming and not upcoming & {"Isla Fraser", "Rhys Owen", "Priya Chandra"}
    assert [w["counterparty"] for w in k["bill_overdue"]] == ["Harbourline Logistics Ltd"]
    # ordering: high first
    sev = [w["severity"] for w in ws]
    assert sev == sorted(sev, key=lambda s: {"high": 0, "medium": 1, "info": 2}[s])


def _tx(i, d, name, amt, direction="credit", typ="PAYMENT"):
    return {"id": f"t{i}", "date": d, "status": "BOOKED", "type": typ, "direction": direction, "reporting_amount": amt, "participant": name, "category": "Revenue"}


def test_short_payment_and_on_time_customer():
    rows = [_tx(1, "2026-07-05", "Acme", 1000), _tx(2, "2026-08-05", "Acme", 1000), _tx(3, "2026-09-05", "Acme", 1000), _tx(4, "2026-10-05", "Acme", 600),
            _tx(5, "2026-07-03", "Beta", 500), _tx(6, "2026-08-03", "Beta", 500), _tx(7, "2026-09-03", "Beta", 500), _tx(8, "2026-10-03", "Beta", 500)]
    ws = [w.to_dict() for w in detect(rows, [], date(2026, 10, 20))]
    assert [(w["kind"], w["counterparty"]) for w in ws] == [("short_payment", "Acme")]


def test_cli_warnings(capsys):
    assert main(["--live", str(LIVE), "warnings"]) == 0
    out = capsys.readouterr().out
    assert "MONEY IN" in out and "MONEY OUT" in out and "charged twice" in out
