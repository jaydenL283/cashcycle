from datetime import date
from pathlib import Path

from cashcycle.insights import COST_KIND, ageing_bucket, build_insights, write_dashboard_files

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live" / "r3con-tech"
AS_OF = date(2026, 10, 20)


def test_creditor_ageing_and_days_to_pay():
    c = build_insights(LIVE, AS_OF)["creditors"]
    assert ageing_bucket(0) == "not yet due" and ageing_bucket(30) == "1-30 days overdue" and ageing_bucket(61) == "over 60 days overdue"
    open_refs = {r["reference"]: r for r in c["open"]}
    assert open_refs["SEN-2026-10-4471"]["days_overdue"] == 15 and open_refs["SEN-2026-10-4471"]["bucket"] == "1-30 days overdue"
    assert open_refs["WW-UK-2026-11-4B"]["bucket"] == "not yet due"
    # the second capture of the Snowflake bill is marked as a duplicate and not counted in ageing
    dup = [r for r in c["open"] if r["reference"] == "SNF-INV-2026-10"]
    assert len(dup) == 2 and sum(r["duplicate_capture"] for r in dup) == 1
    total_counted = sum(b["total"] for b in c["ageing"])
    assert total_counted == c["summary"]["open_gbp_total"]
    assert total_counted == round(sum(r["total"] for r in c["open"] if not r["duplicate_capture"] and r["currency"] == "GBP"), 2)
    paid = {r["reference"]: r for r in c["paid"]}
    assert paid["CM-2026-109"]["days_to_pay"] == 14 and paid["CM-2026-109"]["days_vs_due"] == 0
    assert paid["PST-50231"]["days_vs_due"] == -8  # paid 8 days before due
    assert c["summary"]["avg_days_to_pay"] is not None


def test_year_on_year_has_both_years_for_autumn_months():
    ins = build_insights(LIVE, AS_OF)
    by = {r["month"]: r for r in ins["yoy"]}
    assert by["Sep"]["out_2025"] == 130764.18 and by["Sep"]["out_2026"] == 162333.84
    assert by["Sep"]["out_change_pct"] == 24.1
    assert by["Oct"]["partial"] is True and by["Jan"]["out_2025"] is None
    yt = {r["year"]: r for r in ins["year_totals"]}
    assert yt["2025"]["months_covered"] == 5 and yt["2026"]["months_covered"] == 10


def test_payroll_growth_and_projection():
    p = build_insights(LIVE, AS_OF)["payroll"]
    s = p["summary"]
    assert s["period"] == "2025-08 to 2026-09" and s["employees_on_payroll"] == 3 and s["salaried_total"] == 206000
    assert s["first_3_months_avg"] == 51466.67 and s["last_3_months_avg"] == 59800.0
    assert s["growth_over_period_pct"] == 16.2 and 15 < s["annualised_growth_pct"] < 20
    assert [x["year"] for x in p["projection"]] == [2027, 2028, 2029]
    assert p["projection"][0]["projected_annual_payroll"] > s["current_annual_run_rate"]
    assert p["projection"][2]["projected_annual_payroll"] > p["projection"][0]["projected_annual_payroll"]


def test_fixed_vs_variable():
    k = build_insights(LIVE, AS_OF)["costs"]
    assert COST_KIND["Rent"] == "fixed" and COST_KIND["Hosting & Infrastructure"] == "variable" and COST_KIND["Capital Investments"] == "capital"
    sep = next(m for m in k["by_month"] if m["month"] == "2026-09")
    # fixed in Sep 2026: payroll 69000 + subscriptions 8847.59 + rent 6750 + legal 7940 + bank fees 222.07
    assert sep["fixed"] == round(69000 + 8847.59 + 6750 + 7940 + 222.07, 2)
    assert 50 < k["summary"]["avg_fixed_share_pct"] < 80
    cats = {c["category"]: c for c in k["by_category"]}
    assert cats["Rent"]["swing_pct"] is not None and cats["Capital Investments"]["kind"] == "capital"


def test_dashboard_files_written(tmp_path):
    files = write_dashboard_files(build_insights(LIVE, AS_OF), tmp_path)
    assert len(files) == 15 and all(f.exists() for f in files)
