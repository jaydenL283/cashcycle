"""Further views on the live export: creditors, year on year, payroll growth, fixed vs variable costs, cards.

All arithmetic is here, in code. Projections are labelled as projections.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from cashcycle.data import money
from cashcycle.runway import q1, q2

# How each spend category behaves. Fixed: the same whether the company sells more or not.
# Variable: moves with activity. Capital and tax are shown separately, not as running costs.
COST_KIND = {
    "Rent": "fixed", "Payroll & Benefits": "fixed", "Subscriptions": "fixed", "Legal & Professional Fees": "fixed", "Accounting": "fixed",
    "Insurance": "fixed", "Bank Fees": "fixed", "Licence fees": "fixed", "Legal fees": "fixed", "Payroll Tax": "fixed",
    "Hosting & Infrastructure": "variable", "Marketing & Advertising": "variable", "Travel & Entertainment": "variable", "Food & Dining": "variable",
    "Office Expenses": "variable", "General Expenses": "variable", "Contractors": "variable", "Consulting": "variable", "Motoring/Vehicle expenses": "variable",
    "Building maintenance and repairs": "variable", "Direct Product Costs": "variable", "Import Duties": "variable",
    "Capital Investments": "capital", "Sales Tax / VAT": "tax", "Income Tax": "tax", "Internal Transfers": "excluded", "Interest Income": "excluded",
}


def _read(live: Path, name: str) -> dict:
    return json.loads((live / name).read_text(encoding="utf-8"))


# --- creditors ---------------------------------------------------------------


def ageing_bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "not yet due"
    if days_overdue <= 30:
        return "1-30 days overdue"
    if days_overdue <= 60:
        return "31-60 days overdue"
    return "over 60 days overdue"


def creditors(bills: list[dict], as_of: date) -> dict:
    open_rows, paid_rows = [], []
    seen: set[tuple] = set()
    for b in bills:
        vendor = (b.get("vendor") or {}).get("name", "?")
        a = b.get("amounts") or {}
        total, ccy = money(a.get("total")), a.get("currency", "GBP")
        inv_date = date.fromisoformat(b["dates"]["invoice_date"]) if b.get("dates", {}).get("invoice_date") else None
        due = date.fromisoformat(b["dates"]["due_date"]) if b.get("dates", {}).get("due_date") else None
        if b.get("is_paid") and b.get("paid_at") and inv_date and due:
            paid = date.fromisoformat(b["paid_at"][:10])
            paid_rows.append({"reference": b.get("reference"), "vendor": vendor, "total": float(total), "currency": ccy, "invoice_date": inv_date.isoformat(), "due_date": due.isoformat(), "paid_at": paid.isoformat(), "days_to_pay": (paid - inv_date).days, "days_vs_due": (paid - due).days, "terms_days": (due - inv_date).days})
        elif not b.get("is_paid") and due:
            key = (vendor.lower(), b.get("reference"), total)
            dup = key in seen
            seen.add(key)
            overdue = (as_of - due).days
            open_rows.append({"reference": b.get("reference"), "vendor": vendor, "total": float(total), "currency": ccy, "invoice_date": inv_date.isoformat() if inv_date else None, "due_date": due.isoformat(), "days_overdue": overdue, "bucket": ageing_bucket(overdue), "duplicate_capture": dup, "sync": (b.get("reconciliation") or {}).get("status")})
    open_rows.sort(key=lambda r: (-r["days_overdue"], -r["total"]))
    paid_rows.sort(key=lambda r: r["paid_at"], reverse=True)
    by_vendor: dict[str, dict] = {}
    for r in open_rows:
        if r["duplicate_capture"]:
            continue
        v = by_vendor.setdefault(r["vendor"], {"vendor": r["vendor"], "open_total": 0.0, "open_bills": 0, "oldest_due": r["due_date"], "max_days_overdue": r["days_overdue"], "avg_days_to_pay": None, "paid_bills": 0})
        v["open_total"] = round(v["open_total"] + r["total"], 2)
        v["open_bills"] += 1
        v["oldest_due"] = min(v["oldest_due"], r["due_date"])
        v["max_days_overdue"] = max(v["max_days_overdue"], r["days_overdue"])
    for r in paid_rows:
        v = by_vendor.setdefault(r["vendor"], {"vendor": r["vendor"], "open_total": 0.0, "open_bills": 0, "oldest_due": None, "max_days_overdue": None, "avg_days_to_pay": None, "paid_bills": 0})
        v["paid_bills"] += 1
        v["_dtp"] = v.get("_dtp", []) + [r["days_to_pay"]]
    for v in by_vendor.values():
        if v.get("_dtp"):
            v["avg_days_to_pay"] = round(statistics.mean(v.pop("_dtp")), 1)
    buckets: dict[str, dict] = {}
    for r in open_rows:
        if r["duplicate_capture"] or r["currency"] != "GBP":
            continue
        bk = buckets.setdefault(r["bucket"], {"bucket": r["bucket"], "total": 0.0, "bills": 0})
        bk["total"] = round(bk["total"] + r["total"], 2)
        bk["bills"] += 1
    order = ["not yet due", "1-30 days overdue", "31-60 days overdue", "over 60 days overdue"]
    dtp = [r["days_to_pay"] for r in paid_rows]
    vs_due = [r["days_vs_due"] for r in paid_rows]
    return {
        "open": open_rows,
        "paid": paid_rows,
        "by_vendor": sorted(by_vendor.values(), key=lambda v: (-v["open_total"], v["vendor"])),
        "ageing": [buckets.get(k, {"bucket": k, "total": 0.0, "bills": 0}) for k in order],
        "summary": {"open_gbp_total": round(sum(b["total"] for b in buckets.values()), 2), "open_bills": sum(b["bills"] for b in buckets.values()), "avg_days_to_pay": round(statistics.mean(dtp), 1) if dtp else None, "median_days_to_pay": statistics.median(dtp) if dtp else None, "paid_late_share": round(sum(1 for d in vs_due if d > 0) / len(vs_due), 3) if vs_due else None, "avg_days_vs_due": round(statistics.mean(vs_due), 1) if vs_due else None},
    }


# --- year on year -------------------------------------------------------------


def year_on_year(groups: list[dict], as_of: date) -> list[dict]:
    by: dict[str, dict] = defaultdict(lambda: {"in": 0.0, "out": 0.0})
    for g in groups:
        m = g["group"]["date"][:7]
        by[m]["in" if g["group"]["direction"] == "credit" else "out"] += float(g["metrics"]["total"])
    years = sorted({m[:4] for m in by})
    out = []
    for mo in range(1, 13):
        row = {"month_num": mo, "month": date(2000, mo, 1).strftime("%b")}
        for y in years:
            k = f"{y}-{mo:02d}"
            row[f"in_{y}"] = round(by[k]["in"], 2) if k in by else None
            row[f"out_{y}"] = round(by[k]["out"], 2) if k in by else None
            row[f"net_{y}"] = round(by[k]["in"] - by[k]["out"], 2) if k in by else None
        if len(years) >= 2:
            a, b = years[-2], years[-1]
            row["out_change_pct"] = round((row[f"out_{b}"] - row[f"out_{a}"]) / row[f"out_{a}"] * 100, 1) if row.get(f"out_{a}") and row.get(f"out_{b}") else None
            row["in_change_pct"] = round((row[f"in_{b}"] - row[f"in_{a}"]) / row[f"in_{a}"] * 100, 1) if row.get(f"in_{a}") and row.get(f"in_{b}") else None
        row["partial"] = f"{as_of.year}-{mo:02d}" == as_of.strftime("%Y-%m")
        out.append(row)
    return out


def year_totals(groups: list[dict], as_of: date) -> list[dict]:
    by: dict[str, dict] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "months": set()})
    for g in groups:
        m = g["group"]["date"][:7]
        by[m[:4]]["in" if g["group"]["direction"] == "credit" else "out"] += float(g["metrics"]["total"])
        by[m[:4]]["months"].add(m)
    return [{"year": y, "in": round(v["in"], 2), "out": round(v["out"], 2), "net": round(v["in"] - v["out"], 2), "months_covered": len(v["months"]), "avg_monthly_out": round(v["out"] / len(v["months"]), 2), "avg_monthly_in": round(v["in"] / len(v["months"]), 2)} for y, v in sorted(by.items())]


# --- payroll ------------------------------------------------------------------


def payroll(spend_groups: list[dict], categories: dict[str, str], runs: list[dict], as_of: date, years_ahead: int = 3) -> dict:
    monthly: dict[str, float] = defaultdict(float)
    for g in spend_groups:
        leaf = categories.get(g["group"]["category_id"], "").split(" › ")[-1]
        if leaf in ("Payroll & Benefits", "Payroll Tax"):
            monthly[g["group"]["date"][:7]] += float(g["metrics"]["total"])
    months = sorted(monthly)
    full = [m for m in months if m < as_of.strftime("%Y-%m")]
    series = [{"month": m, "payroll": round(monthly[m], 2), "partial": m not in full} for m in months]
    first3 = [monthly[m] for m in full[:3]]
    last3 = [monthly[m] for m in full[-3:]]
    span_months = max(1, len(full) - 3)
    growth_total = (statistics.mean(last3) / statistics.mean(first3) - 1) if first3 and last3 else 0.0
    annual_growth = (1 + growth_total) ** (12 / span_months) - 1 if span_months else 0.0
    run_rate_monthly = statistics.mean(last3) if last3 else 0.0
    base_year = as_of.year
    projection = []
    for n in range(1, years_ahead + 1):
        projection.append({"year": base_year + n, "projected_annual_payroll": round(run_rate_monthly * 12 * (1 + annual_growth) ** n, 2), "projected_monthly": round(run_rate_monthly * (1 + annual_growth) ** n, 2), "basis": f"run-rate £{run_rate_monthly:,.0f}/month growing {annual_growth * 100:.1f}% a year"})
    people = []
    latest = max(runs, key=lambda r: r["pay_date"]) if runs else None
    if latest:
        for e in latest["employees"]:
            people.append({"name": e["name"], "role": e["role"], "annual_salary": e["annual_salary"], "monthly_net_pay": e["net_pay"]})
    return {
        "monthly": series,
        "summary": {
            "first_3_months_avg": round(statistics.mean(first3), 2) if first3 else None,
            "last_3_months_avg": round(run_rate_monthly, 2),
            "growth_over_period_pct": round(growth_total * 100, 1),
            "annualised_growth_pct": round(annual_growth * 100, 1),
            "period": f"{full[0]} to {full[-1]}" if full else None,
            "current_annual_run_rate": round(run_rate_monthly * 12, 2),
            "employees_on_payroll": latest["employee_count"] if latest else None,
            "salaried_total": sum(p["annual_salary"] for p in people),
            "net_pay_per_run": latest["total_net_pay"] if latest else None,
            "note": "Payroll & Benefits and Payroll Tax categories, which include contractor payroll (Remote People, Fathom, Deel) as well as the 3 salaried employees on Seapoint pay runs.",
        },
        "projection": projection,
        "people": people,
    }


# --- fixed vs variable ----------------------------------------------------------


def costs(spend_groups: list[dict], categories: dict[str, str], as_of: date) -> dict:
    rows = []
    by_month_kind: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    by_cat: dict[str, dict] = {}
    full_cut = as_of.strftime("%Y-%m")
    for g in spend_groups:
        name = categories.get(g["group"]["category_id"], "Uncategorised")
        leaf = name.split(" › ")[-1]
        kind = COST_KIND.get(leaf, "variable")
        m = g["group"]["date"][:7]
        t = float(g["metrics"]["total"])
        rows.append({"month": m, "category": leaf, "group": name.split(" › ")[0], "kind": kind, "total": round(t, 2), "partial": m == full_cut})
        if kind in ("fixed", "variable"):
            by_month_kind[m][kind] += t
        c = by_cat.setdefault(leaf, {"category": leaf, "kind": kind, "total_12m": 0.0, "months": 0, "min_month": None, "max_month": None})
        if m < full_cut:
            c["total_12m"] += t
            c["months"] += 1
            c["min_month"] = t if c["min_month"] is None else min(c["min_month"], t)
            c["max_month"] = t if c["max_month"] is None else max(c["max_month"], t)
    months = sorted(by_month_kind)
    kinds = [{"month": m, "fixed": round(by_month_kind[m]["fixed"], 2), "variable": round(by_month_kind[m]["variable"], 2), "fixed_share": round(by_month_kind[m]["fixed"] / (by_month_kind[m]["fixed"] + by_month_kind[m]["variable"]) * 100, 1) if (by_month_kind[m]["fixed"] + by_month_kind[m]["variable"]) else None, "partial": m == full_cut} for m in months]
    full = [k for k in kinds if not k["partial"]]
    cats = []
    for c in by_cat.values():
        avg = c["total_12m"] / c["months"] if c["months"] else 0.0
        swing = (c["max_month"] - c["min_month"]) / avg * 100 if avg and c["max_month"] is not None else None
        cats.append({"category": c["category"], "kind": c["kind"], "avg_monthly": round(avg, 2), "total": round(c["total_12m"], 2), "months": c["months"], "swing_pct": round(swing, 1) if swing is not None else None})
    cats.sort(key=lambda c: -c["avg_monthly"])
    return {
        "by_month": kinds,
        "by_category": cats,
        "summary": {
            "avg_fixed_monthly": round(statistics.mean(k["fixed"] for k in full), 2) if full else None,
            "avg_variable_monthly": round(statistics.mean(k["variable"] for k in full), 2) if full else None,
            "avg_fixed_share_pct": round(statistics.mean(k["fixed_share"] for k in full if k["fixed_share"] is not None), 1) if full else None,
            "months": f"{full[0]['month']} to {full[-1]['month']}" if full else None,
            "note": "Classified by category in code (COST_KIND in insights.py). Capital purchases and VAT are shown separately, not as running costs.",
        },
    }


def build_insights(live: Path, as_of: date) -> dict:
    from cashcycle.leverage import build_leverage

    cats = {c["id"]: c["name"] for c in _read(live, "categories.json")["data"]}
    spend = _read(live, "spend_by_category_month.json")["data"]
    monthly = _read(live, "monthly_in_out_all_time.json")["data"]
    bills = _read(live, "invoices.json")["data"]
    runs = _read(live, "payroll_runs.json")["data"]
    vendors = _read(live, "card_spend_by_vendor.json")["data"]
    return {
        "as_of": as_of.isoformat(),
        "creditors": creditors(bills, as_of),
        "yoy": year_on_year(monthly, as_of),
        "year_totals": year_totals(monthly, as_of),
        "payroll": payroll(spend, cats, runs, as_of),
        "costs": costs(spend, cats, as_of),
        "card_vendors": sorted(vendors, key=lambda v: -v["total"]),
        "leverage": build_leverage(live, as_of),
        "warnings": __import__("cashcycle.early_warning", fromlist=["build_warnings"]).build_warnings(live, as_of),
    }


def write_dashboard_files(ins: dict, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "creditors_open.json": ins["creditors"]["open"],
        "creditors_paid.json": ins["creditors"]["paid"],
        "creditors_by_vendor.json": ins["creditors"]["by_vendor"],
        "creditors_ageing.json": ins["creditors"]["ageing"],
        "creditors_summary.json": [{"metric": k, "value": v} for k, v in ins["creditors"]["summary"].items()],
        "yoy_months.json": ins["yoy"],
        "year_totals.json": ins["year_totals"],
        "payroll_monthly.json": ins["payroll"]["monthly"],
        "payroll_projection.json": ins["payroll"]["projection"],
        "payroll_people.json": ins["payroll"]["people"],
        "payroll_summary.json": [{"metric": k, "value": v} for k, v in ins["payroll"]["summary"].items()],
        "costs_by_month.json": ins["costs"]["by_month"],
        "costs_by_category.json": ins["costs"]["by_category"],
        "costs_summary.json": [{"metric": k, "value": v} for k, v in ins["costs"]["summary"].items()],
        "card_vendors.json": ins["card_vendors"],
        "leverage_rows.json": ins["leverage"]["rows"],
        "leverage_by_role.json": ins["leverage"]["by_role"],
        "leverage_summary.json": [{"metric": k, "value": v} for k, v in ins["leverage"]["summary"].items()],
        "leverage_opportunities.json": ins["leverage"]["opportunities"],
        "warnings.json": [{**w, "id": i + 1} for i, w in enumerate(ins["warnings"])],
    }
    written = []
    for name, data in files.items():
        p = out / name
        p.write_text(json.dumps(data), encoding="utf-8")
        written.append(p)
    return written
