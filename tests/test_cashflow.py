from datetime import date
from decimal import Decimal
from pathlib import Path

from cashcycle.cashflow import build_cashflow, detect_recurring, forecast, flows_by_method
from cashcycle.cli import main

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live" / "r3con-tech"


def test_recurring_detection_and_days():
    d = build_cashflow(LIVE, date(2026, 10, 20))
    rec_in = {r["name"]: r for r in d["recurring_in"]}
    rec_out = {r["name"]: r for r in d["recurring_out"]}
    assert rec_in["Acme Corp"]["usual_day"] == 4 and rec_in["Acme Corp"]["typical_amount"] == 2360.0
    assert rec_out["Snowflake"]["method"] == "direct debit" and rec_out["Snowflake"]["usual_day"] == 11 and rec_out["Snowflake"]["typical_amount"] == 21800.0
    assert rec_out["Remote People Ltd"]["method"] == "bank transfer" and rec_out["Remote People Ltd"]["usual_day"] == 25
    assert rec_out["HSBC UK"]["method"] == "bank fee"


def test_method_shares_sum_to_full_month_spend():
    d = build_cashflow(LIVE, date(2026, 10, 20))
    share = d["out_share_full_months"]
    assert round(sum(v["share_pct"] for v in share.values())) == 100
    assert share["bank transfer"]["total"] == 337253.17 and share["direct debit"]["total"] == 147140.8
    assert d["card_daily_run_rate"] == 594.86  # (16661.57 + 16309.94 + 20565.51) / 3 / 30


def test_forecast_places_recurring_items_and_bills():
    d = build_cashflow(LIVE, date(2026, 10, 20))
    days = {x["date"]: x for x in d["forecast_days"]}
    assert d["opening_cash"] == 2459109.84
    assert any("Remote People" in i for i in days["2026-10-25"]["items"])  # payroll on the 25th
    assert any("Snowflake" in i for i in days["2026-11-11"]["items"])  # direct debit on the 11th
    assert not any("Snowflake" in i for i in days["2026-10-21"]["items"])  # October already paid
    assert any("FTP-0931" in i for i in days["2026-10-21"]["items"])  # open bill not covered by a recurring payment
    assert days["2026-11-04"]["inflow"] == 2360.0  # Acme Corp pays on the 4th
    assert len(d["forecast_days"]) == 90 and len(d["forecast_weekly"]) == 13
    assert d["closing_balance_90d"] < d["opening_cash"]


def test_cashflow_cli_writes_dashboard_files(tmp_path):
    assert main(["--live", str(LIVE), "cashflow", "--out-dir", str(tmp_path)]) == 0
    for name in ("kpis.json", "flows_by_month.json", "recurring.json", "forecast_days.json", "forecast_weekly.json", "cashflow.json"):
        assert (tmp_path / name).exists()
