"""Spend ranked by the value it creates, cross-referenced with fixed vs variable.

Nothing in the ledger says what a cost "created", so this is a scored
framework with the judgement calls written down (VALUE_ROLE, ROLE_WEIGHT)
and the arithmetic in code. The ranking says where an extra pound is most
likely to compound; it does not say the spend is working, which needs
attribution the data does not carry yet (see `evidence` on each row).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from cashcycle.insights import COST_KIND, _read

# What each category is for. Growth: wins customers. Delivery: runs the product customers pay for.
# Build: makes the product better (people, equipment). Enable: helps the team work. Overhead: keeps the company legal and housed.
VALUE_ROLE = {
    "Marketing & Advertising": "growth",
    "Hosting & Infrastructure": "delivery",
    "Payroll & Benefits": "build", "Payroll Tax": "build", "Capital Investments": "build", "Contractors": "build", "Consulting": "build",
    "Subscriptions": "enable", "Travel & Entertainment": "enable", "Food & Dining": "enable", "Office Expenses": "enable", "Motoring/Vehicle expenses": "enable",
    "Rent": "overhead", "Legal & Professional Fees": "overhead", "Legal fees": "overhead", "Accounting": "overhead", "Insurance": "overhead", "Bank Fees": "overhead", "General Expenses": "overhead", "Licence fees": "overhead", "Building maintenance and repairs": "overhead",
    "Sales Tax / VAT": "pass-through", "Income Tax": "pass-through", "Import Duties": "pass-through",
}
ROLE_WEIGHT = {"growth": 1.0, "delivery": 0.8, "build": 0.7, "enable": 0.4, "overhead": 0.2, "pass-through": 0.0}
KIND_WEIGHT = {"variable": 1.0, "fixed": 0.6, "capital": 0.7, "tax": 0.0}


def _corr(a: list[float], b: list[float]) -> float | None:
    if len(a) < 4 or len(a) != len(b) or statistics.pstdev(a) == 0 or statistics.pstdev(b) == 0:
        return None
    return round(statistics.correlation(a, b), 2)


def rank_spend(spend_groups: list[dict], categories: dict[str, str], monthly_groups: list[dict], as_of: date) -> dict:
    cut = as_of.strftime("%Y-%m")
    by_cat: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for g in spend_groups:
        m = g["group"]["date"][:7]
        if m >= cut:
            continue
        leaf = categories.get(g["group"]["category_id"], "Uncategorised").split(" › ")[-1]
        by_cat[leaf][m] += float(g["metrics"]["total"])
    revenue: dict[str, float] = defaultdict(float)
    for g in monthly_groups:
        m = g["group"]["date"][:7]
        if g["group"]["direction"] == "credit" and m < cut:
            revenue[m] += float(g["metrics"]["total"])
    months = sorted({m for v in by_cat.values() for m in v})
    rev_series = [revenue.get(m, 0.0) for m in months]
    opex_total = sum(sum(v.values()) for k, v in by_cat.items() if COST_KIND.get(k, "variable") in ("fixed", "variable"))
    rows = []
    for cat, series in by_cat.items():
        kind = COST_KIND.get(cat, "variable")
        role = VALUE_ROLE.get(cat, "enable")
        vals = [series.get(m, 0.0) for m in months]
        avg = sum(vals) / len(months)
        first3 = [v for v in vals[:3]] ; last3 = [v for v in vals[-3:]]
        trend = (statistics.mean(last3) / statistics.mean(first3) - 1) * 100 if statistics.mean(first3) else None
        score = round(ROLE_WEIGHT[role] * KIND_WEIGHT.get(kind, 0.5) * 100)
        rows.append({
            "category": cat, "role": role, "kind": kind,
            "avg_monthly": round(avg, 2), "total": round(sum(vals), 2),
            "share_of_opex_pct": round(sum(vals) / opex_total * 100, 1) if kind in ("fixed", "variable") and opex_total else None,
            "trend_pct": round(trend, 1) if trend is not None else None,
            "revenue_correlation": _corr(vals, rev_series),
            "leverage_score": score,
            "evidence": f"{len(months)} full months {months[0]} to {months[-1]}; role and kind are judgement calls in leverage.py",
        })
    rows.sort(key=lambda r: (-r["leverage_score"], -r["avg_monthly"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    by_role: dict[str, dict] = {}
    for r in rows:
        if r["kind"] not in ("fixed", "variable"):
            continue
        b = by_role.setdefault(r["role"], {"role": r["role"], "avg_monthly": 0.0, "share_pct": 0.0, "fixed": 0.0, "variable": 0.0, "weight": ROLE_WEIGHT[r["role"]]})
        b["avg_monthly"] = round(b["avg_monthly"] + r["avg_monthly"], 2)
        b[r["kind"]] = round(b[r["kind"]] + r["avg_monthly"], 2)
    for b in by_role.values():
        b["share_pct"] = round(b["avg_monthly"] / (opex_total / len(months)) * 100, 1) if opex_total else None
    rev_avg = statistics.mean(rev_series) if rev_series else 0.0
    growth_spend = by_role.get("growth", {}).get("avg_monthly", 0.0)
    summary = {
        "months": f"{months[0]} to {months[-1]}",
        "avg_monthly_opex": round(opex_total / len(months), 2),
        "avg_monthly_revenue": round(rev_avg, 2),
        "revenue_covers_opex_pct": round(rev_avg / (opex_total / len(months)) * 100, 1) if opex_total else None,
        "growth_spend_monthly": growth_spend,
        "revenue_per_growth_pound": round(rev_avg / growth_spend, 2) if growth_spend else None,
        "overhead_plus_enable_monthly": round(by_role.get("overhead", {}).get("avg_monthly", 0.0) + by_role.get("enable", {}).get("avg_monthly", 0.0), 2),
    }
    return {"rows": rows, "by_role": sorted(by_role.values(), key=lambda b: -b["weight"]), "summary": summary, "opportunities": opportunities(rows, by_role, summary)}


def opportunities(rows: list[dict], by_role: dict[str, dict], summary: dict) -> list[dict]:
    """Rule-based, with the numbers that triggered each one. Judgement, for a person to weigh."""
    out = []
    cat = {r["category"]: r for r in rows}
    g = cat.get("Marketing & Advertising")
    if g:
        flat = g["trend_pct"] is not None and abs(g["trend_pct"]) < 15
        out.append({"priority": 1, "area": "Marketing & Advertising", "kind": g["kind"], "role": "growth", "action": "increase, but only behind attribution",
                    "why": f"Highest-leverage line (growth, variable) at £{g['avg_monthly']:,.0f}/month, {'flat' if flat else 'moving'} over the period while revenue averages £{summary['avg_monthly_revenue']:,.0f}/month ({summary['revenue_per_growth_pound']} of revenue per £1 of growth spend). Revenue correlation {g['revenue_correlation']} is weak, so first tag each campaign to the customers it wins, then scale what pays back.",
                    "monthly_amount": g["avg_monthly"]})
    h = cat.get("Hosting & Infrastructure")
    if h:
        out.append({"priority": 2, "area": "Hosting & Infrastructure", "kind": h["kind"], "role": "delivery", "action": "protect, and buy down unit cost",
                    "why": f"£{h['avg_monthly']:,.0f}/month and up {h['trend_pct']:+.0f}% over the period, which is the clearest sign of usage growth in the ledger. Snowflake alone is a fixed £21,800 direct debit on the 11th: a committed-use contract or a right-sizing review is cheaper than cutting capacity.",
                    "monthly_amount": h["avg_monthly"]})
    t = cat.get("Travel & Entertainment")
    f = cat.get("Food & Dining")
    if t:
        out.append({"priority": 3, "area": "Travel & Entertainment, Food & Dining", "kind": "variable", "role": "enable", "action": "trim and redirect to growth",
                    "why": f"Variable and enabling, so the easiest money to move: £{t['avg_monthly']:,.0f}/month travel plus £{(f or {}).get('avg_monthly', 0):,.0f}/month food, about {(t['share_of_opex_pct'] or 0) + ((f or {}).get('share_of_opex_pct') or 0):.0f}% of running costs. Cutting a quarter funds roughly £{(t['avg_monthly'] + (f or {}).get('avg_monthly', 0)) / 4:,.0f}/month of marketing or a contractor day a week.",
                    "monthly_amount": t["avg_monthly"] + (f or {}).get("avg_monthly", 0)})
    p = cat.get("Payroll & Benefits")
    if p:
        out.append({"priority": 4, "area": "Payroll & Benefits", "kind": p["kind"], "role": "build", "action": "grow deliberately, tie each hire to delivery or growth",
                    "why": f"Largest line at £{p['avg_monthly']:,.0f}/month ({p['share_of_opex_pct']}% of running costs), fixed, and up {p['trend_pct']:+.0f}% over the period. It is the real lever on what gets built, so each new role should name the product or revenue outcome it unlocks; contractor payroll (Remote People, Fathom, Deel) is the flexible part.",
                    "monthly_amount": p["avg_monthly"]})
    s = cat.get("Subscriptions")
    if s and s["trend_pct"] and s["trend_pct"] > 20:
        out.append({"priority": 5, "area": "Subscriptions", "kind": s["kind"], "role": "enable", "action": "review the recent jump",
                    "why": f"Up {s['trend_pct']:+.0f}% over the period to £{s['avg_monthly']:,.0f}/month average; September alone was higher than any earlier month. Leakfinder's duplicate and unused-seat checks apply here.",
                    "monthly_amount": s["avg_monthly"]})
    o = by_role.get("overhead")
    if o:
        out.append({"priority": 6, "area": "Overhead (rent, legal, accounting, bank fees, office)", "kind": "mostly fixed", "role": "overhead", "action": "hold flat",
                    "why": f"£{o['avg_monthly']:,.0f}/month, {o['share_pct']}% of running costs. Lowest leverage: a pound saved here is worth a pound, a pound added creates nothing. Keep it flat as the company grows so its share falls.",
                    "monthly_amount": o["avg_monthly"]})
    return out


def build_leverage(live: Path, as_of: date) -> dict:
    cats = {c["id"]: c["name"] for c in _read(live, "categories.json")["data"]}
    return rank_spend(_read(live, "spend_by_category_month.json")["data"], cats, _read(live, "monthly_in_out_all_time.json")["data"], as_of)
