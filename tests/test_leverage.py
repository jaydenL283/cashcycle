from datetime import date
from pathlib import Path

from cashcycle.leverage import ROLE_WEIGHT, VALUE_ROLE, build_leverage

LIVE = Path(__file__).resolve().parents[1] / "data" / "live" / "r3con-tech"


def test_ranking_puts_growth_variable_first_and_overhead_last():
    lv = build_leverage(LIVE, date(2026, 10, 20))
    rows = lv["rows"]
    assert rows[0]["category"] == "Marketing & Advertising" and rows[0]["leverage_score"] == 100
    assert rows[1]["category"] == "Hosting & Infrastructure" and rows[1]["leverage_score"] == 80
    running = [r for r in rows if r["kind"] in ("fixed", "variable")]
    assert running[-1]["role"] == "overhead" and running[-1]["leverage_score"] == 12
    assert all(rows[i]["leverage_score"] >= rows[i + 1]["leverage_score"] for i in range(len(rows) - 1))
    by = {r["category"]: r for r in rows}
    assert by["Payroll & Benefits"]["kind"] == "fixed" and by["Payroll & Benefits"]["role"] == "build" and by["Payroll & Benefits"]["leverage_score"] == 42
    assert by["Sales Tax / VAT"]["leverage_score"] == 0


def test_shares_and_summary():
    lv = build_leverage(LIVE, date(2026, 10, 20))
    running = [r for r in lv["rows"] if r["kind"] in ("fixed", "variable")]
    assert round(sum(r["share_of_opex_pct"] for r in running)) == 100
    s = lv["summary"]
    assert s["months"] == "2025-08 to 2026-09" and s["avg_monthly_revenue"] > 0 and s["revenue_covers_opex_pct"] < 50
    assert s["revenue_per_growth_pound"] is not None
    roles = {b["role"]: b for b in lv["by_role"]}
    assert roles["growth"]["variable"] > 0 and roles["overhead"]["fixed"] > 0


def test_opportunities_are_numbered_and_cite_numbers():
    lv = build_leverage(LIVE, date(2026, 10, 20))
    ops = lv["opportunities"]
    assert [o["priority"] for o in ops] == list(range(1, len(ops) + 1))
    assert ops[0]["area"] == "Marketing & Advertising" and "attribution" in ops[0]["action"]
    assert any("Snowflake" in o["why"] for o in ops)
    assert all("£" in o["why"] for o in ops)
    assert all(k in VALUE_ROLE for k in ("Rent", "Marketing & Advertising")) and ROLE_WEIGHT["growth"] == 1.0
