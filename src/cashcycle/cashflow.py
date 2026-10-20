"""Cash-flow view: how money comes in, how it goes out, and a short forecast.

Built only from live Seapoint exports (transactions since June, monthly totals
by payment type, accounts). Recurring items are detected in code; the forecast
is the recurring items placed on their usual day plus the card run-rate.
Everything is an estimate from past patterns and is labelled as such.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from cashcycle.data import money
from cashcycle.runway import q2
from cashcycle.seapoint import eligible_cash

METHOD_LABELS = {
    "PAYMENT": "bank transfer",
    "DIRECT_DEBIT": "direct debit",
    "CARD_PAYMENT": "card",
    "FEE": "bank fee",
    "TRANSFER": "transfer",
    "TAX": "tax",
    "PAYOUT": "payout",
    "DIRECT_DEBIT_IN": "direct debit",
}
NON_SETTLING = {"FAILED", "CANCELLED", "FULL_REVERSAL"}


def _month(d: str) -> str:
    return d[:7]


@dataclass
class MethodMonth:
    month: str
    direction: str
    method: str
    total: Decimal
    rows: int


def flows_by_method(groups: list[dict]) -> list[MethodMonth]:
    out = []
    for g in groups:
        grp = g["group"]
        out.append(MethodMonth(_month(grp["date"]), grp["direction"], METHOD_LABELS.get(grp["type"], grp["type"].lower()), money(g["metrics"]["total"]), int(g["metrics"].get("rows", 0))))
    return sorted(out, key=lambda m: (m.month, m.direction, m.method))


@dataclass
class Recurring:
    name: str
    direction: str  # credit = money in, debit = money out
    method: str
    category: str
    months_seen: list[str]
    usual_day: int
    typical_amount: Decimal
    last_amount: Decimal
    last_date: str
    transactions: list[str] = field(default_factory=list)

    def next_dates(self, start: date, end: date) -> list[date]:
        out = []
        y, m = start.year, start.month
        for _ in range(0, 6):
            last_day = (date(y + (m // 12), m % 12 + 1, 1) - timedelta(days=1)).day
            d = date(y, m, min(self.usual_day, last_day))
            if start <= d <= end and _month(d.isoformat()) not in self.months_seen:
                out.append(d)
            elif start <= d <= end and _month(d.isoformat()) in self.months_seen and d > date.fromisoformat(self.last_date):
                out.append(d)
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    def to_dict(self) -> dict:
        return {"name": self.name, "direction": self.direction, "method": self.method, "category": self.category, "months_seen": self.months_seen, "usual_day": self.usual_day, "typical_amount": float(self.typical_amount), "last_amount": float(self.last_amount), "last_date": self.last_date}


def detect_recurring(rows: list[dict], min_months: int = 2) -> list[Recurring]:
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("status") in NON_SETTLING or r.get("internal_transfer"):
            continue
        name = r.get("participant") or r.get("description") or "unknown"
        by_key[(r["direction"], name, r["type"])].append(r)
    out = []
    for (direction, name, typ), txs in by_key.items():
        months = sorted({_month(t["date"]) for t in txs})
        if len(months) < min_months:
            continue
        # one amount per month (a vendor charged twice in a month is summed), then the median
        per_month: dict[str, Decimal] = defaultdict(Decimal)
        days: list[int] = []
        for t in txs:
            per_month[_month(t["date"])] += money(t["reporting_amount"])
            days.append(int(t["date"][8:10]))
        typical = q2(Decimal(statistics.median([float(v) for v in per_month.values()])))
        last = sorted(txs, key=lambda t: t["date"])[-1]
        out.append(Recurring(name, direction, METHOD_LABELS.get(typ, typ.lower()), txs[0].get("category") or "", months, int(statistics.median(days)), typical, money(last["reporting_amount"]), last["date"], [t["id"] for t in txs]))
    return sorted(out, key=lambda r: (r.direction, -r.typical_amount))


@dataclass
class ForecastDay:
    day: date
    inflow: Decimal
    outflow: Decimal
    balance: Decimal
    items: list[str]

    def to_dict(self) -> dict:
        return {"date": self.day.isoformat(), "inflow": float(self.inflow), "outflow": float(self.outflow), "balance": float(self.balance), "items": self.items}


def card_daily_run_rate(flows: list[MethodMonth], as_of: date, months: int = 3) -> Decimal:
    full = sorted({f.month for f in flows if f.month < as_of.strftime("%Y-%m")})[-months:]
    totals = [f.total for f in flows if f.direction == "debit" and f.method == "card" and f.month in full]
    if not totals:
        return Decimal("0")
    return q2(sum(totals, Decimal("0")) / Decimal(len(totals)) / Decimal(30))


def forecast(opening_cash: Decimal, recurring: list[Recurring], flows: list[MethodMonth], open_bills: list[dict], as_of: date, days: int = 90) -> list[ForecastDay]:
    end = as_of + timedelta(days=days)
    per_day_in: dict[date, Decimal] = defaultdict(Decimal)
    per_day_out: dict[date, Decimal] = defaultdict(Decimal)
    items: dict[date, list[str]] = defaultdict(list)
    # months each recurring payee is already covered for: seen in the data, or placed by the forecast
    covered: dict[str, set[str]] = defaultdict(set)
    for r in recurring:
        if r.direction == "debit":
            covered[r.name.lower().split(" ")[0]] |= set(r.months_seen)
        for d in r.next_dates(as_of + timedelta(days=1), end):
            if r.direction == "credit":
                per_day_in[d] += r.typical_amount
            else:
                per_day_out[d] += r.typical_amount
                covered[r.name.lower().split(" ")[0]].add(_month(d.isoformat()))
            items[d].append(f"{r.name} ({r.method}) {'+' if r.direction == 'credit' else '-'}£{r.typical_amount:,.0f}")
    seen_bills: set[tuple] = set()
    for b in open_bills:
        if b.get("is_paid"):
            continue
        due = date.fromisoformat(b["dates"]["due_date"])
        vendor = (b.get("vendor") or {}).get("name", "")
        key = (vendor.lower(), b.get("reference"), money(b["amounts"]["total"]))
        first_word = vendor.lower().split(" ")[0]
        if key in seen_bills or (first_word in covered and _month(max(due, as_of + timedelta(days=1)).isoformat()) in covered[first_word]):
            continue  # a duplicate capture, or a payee the recurring pattern already covers that month
        seen_bills.add(key)
        when = max(due, as_of + timedelta(days=1))
        if when > end or b["amounts"].get("currency") != "GBP":
            continue
        per_day_out[when] += money(b["amounts"]["total"])
        items[when].append(f"bill {b.get('reference')} {vendor} -£{money(b['amounts']['total']):,.0f}")
    rate = card_daily_run_rate(flows, as_of)
    out, bal = [], opening_cash
    d = as_of
    for _ in range(days):
        d += timedelta(days=1)
        inflow, outflow = per_day_in[d], per_day_out[d] + rate
        bal = q2(bal + inflow - outflow)
        out.append(ForecastDay(d, inflow, q2(outflow), bal, items[d]))
    return out


def build_cashflow(live_dir: Path, as_of: date) -> dict:
    def read(name: str) -> dict:
        return json.loads((live_dir / name).read_text(encoding="utf-8"))

    acc, flows_raw, tx, bills = read("accounts.json"), read("money_flows_by_type.json"), read("transactions_non_card.json"), read("invoices.json")
    cash = eligible_cash(acc["data"])
    flows = flows_by_method(flows_raw["data"])
    rec = detect_recurring(tx["data"])
    fc = forecast(cash.eligible_cash, rec, flows, bills["data"], as_of)

    months = sorted({f.month for f in flows})
    full_months = [m for m in months if m < as_of.strftime("%Y-%m")]
    by_month: dict[str, dict] = {m: {"in": {}, "out": {}} for m in months}
    for f in flows:
        by_month[f.month]["in" if f.direction == "credit" else "out"][f.method] = {"total": float(f.total), "rows": f.rows}
    out_share: dict[str, Decimal] = defaultdict(Decimal)
    for f in flows:
        if f.direction == "debit" and f.month in full_months:
            out_share[f.method] += f.total
    out_total = sum(out_share.values(), Decimal("0"))

    inflows = [r for r in rec if r.direction == "credit"]
    outflows = [r for r in rec if r.direction == "debit"]
    low = min(fc, key=lambda d: d.balance)
    weekly = []
    for i in range(0, len(fc), 7):
        chunk = fc[i : i + 7]
        weekly.append({"week_start": chunk[0].day.isoformat(), "inflow": float(sum((c.inflow for c in chunk), Decimal("0"))), "outflow": float(sum((c.outflow for c in chunk), Decimal("0"))), "closing_balance": float(chunk[-1].balance)})
    return {
        "as_of": as_of.isoformat(),
        "organisation": acc.get("organisation_name"),
        "source": "live (Seapoint MCP export fetched " + str(acc.get("fetched_at")) + ")",
        "opening_cash": float(cash.eligible_cash),
        "months": months,
        "full_months": full_months,
        "by_month": by_month,
        "out_share_full_months": {k: {"total": float(v), "share_pct": round(float(v / out_total * 100), 1) if out_total else 0} for k, v in sorted(out_share.items(), key=lambda kv: -kv[1])},
        "card_daily_run_rate": float(card_daily_run_rate(flows, as_of)),
        "recurring_in": [r.to_dict() for r in inflows],
        "recurring_out": [r.to_dict() for r in outflows],
        "forecast_days": [d.to_dict() for d in fc],
        "forecast_weekly": weekly,
        "lowest_point": {"date": low.day.isoformat(), "balance": float(low.balance)},
        "closing_balance_90d": float(fc[-1].balance),
        "notes": [
            "Recurring = same counterparty and payment method in at least 2 months since June; amount is the median month; day is the median day.",
            "Forecast = opening eligible cash + recurring inflows - recurring outflows on their usual days - card spend at the 3-month daily average - open GBP supplier bills on their due date (vendors already covered by a direct debit are skipped).",
            "One-off payments (e.g. HMRC, Deel) are not forecast unless they recur. Estimates, not commitments.",
            f"{as_of.strftime('%Y-%m')} is a partial month.",
        ],
    }
