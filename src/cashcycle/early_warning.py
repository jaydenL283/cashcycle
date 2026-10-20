"""Early warnings from payment patterns.

Money in: a customer who usually pays on a given day and cadence has not paid,
or paid less than usual. Money out: a creditor payment that is missing, has
been taken twice, is much larger than usual, or goes to a new payee for a large
amount; plus large outgoings due in the next week and supplier bills well past due.

Patterns are learnt from the payee's own history (median day, median amount,
month cadence). Each warning carries the expected value, what happened, and
the transaction ids behind it, so a person can check it in one click.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from cashcycle.insights import _read

NON_SETTLING = {"FAILED", "CANCELLED", "FULL_REVERSAL"}
METHOD = {"PAYMENT": "bank transfer", "DIRECT_DEBIT": "direct debit", "FEE": "bank fee", "TRANSFER": "transfer", "CARD_PAYMENT": "card"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


@dataclass
class Warning:
    severity: str  # high | medium | info
    direction: str  # in | out
    kind: str
    counterparty: str
    title: str
    detail: str
    expected_date: str | None = None
    expected_amount: float | None = None
    actual_amount: float | None = None
    days_late: int | None = None
    confidence: str = "medium"
    transactions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["transactions"] = ", ".join(self.transactions)
        return d


@dataclass
class Pattern:
    name: str
    direction: str
    method: str
    category: str
    months: list[str]
    cadence_months: int
    usual_day: int
    typical_amount: float
    single_amounts: list[float]
    tx: list[dict]

    @property
    def confidence(self) -> str:
        return "high" if len(self.months) >= 4 else "medium" if len(self.months) >= 3 else "low"


def _m(d: str) -> str:
    return d[:7]


def _add_months(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) + n
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def _day_in(ym: str, day: int) -> date:
    y, m = int(ym[:4]), int(ym[5:7])
    last = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(day, last))


def _month_gap(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def learn_patterns(rows: list[dict], before: str) -> dict[tuple, Pattern]:
    """Patterns from history strictly before month `before`, so this month's behaviour is tested against them."""
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("status") in NON_SETTLING or r.get("internal_transfer"):
            continue
        by[(r["direction"], r.get("participant") or r.get("description") or "unknown", r["type"])].append(r)
    out = {}
    for key, txs in by.items():
        hist = [t for t in txs if _m(t["date"]) < before]
        months = sorted({_m(t["date"]) for t in hist})
        if len(months) < 2:
            continue
        gaps = [_month_gap(a, b) for a, b in zip(months, months[1:])]
        cadence = int(statistics.median(gaps)) if gaps else 1
        per_month: dict[str, float] = defaultdict(float)
        for t in hist:
            per_month[_m(t["date"])] += float(t["reporting_amount"])
        out[key] = Pattern(key[1], "in" if key[0] == "credit" else "out", METHOD.get(key[2], key[2].lower()), hist[0].get("category") or "", months, max(1, cadence),
                           int(statistics.median(int(t["date"][8:10]) for t in hist)), round(statistics.median(per_month.values()), 2), [float(t["reporting_amount"]) for t in hist], txs)
    return out


MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], start=1)}


def _run_period(name: str) -> str | None:
    parts = name.split()
    if len(parts) >= 2 and parts[0] in MONTHS and parts[1].isdigit():
        return f"{int(parts[1]):04d}-{MONTHS[parts[0]]:02d}"
    return None


def detect(rows: list[dict], bills: list[dict], as_of: date, *, payroll_runs: list[dict] | None = None, grace_in: int = 1, grace_out: int = 2, lookahead_days: int = 7, big_new_payee: float = 5000.0) -> list[Warning]:
    this_month = as_of.strftime("%Y-%m")
    patterns = learn_patterns(rows, this_month)
    warnings: list[Warning] = []
    open_bills = [b for b in bills if not b.get("is_paid") and (b.get("dates") or {}).get("due_date")]

    def bills_for(name: str) -> list[dict]:
        w = name.lower().split(" ")[0]
        return [b for b in open_bills if (b.get("vendor") or {}).get("name", "").lower().startswith(w)]

    prepaid: set[str] = set()
    for run in payroll_runs or []:
        period = _run_period(run.get("name", ""))
        if not period or run.get("status") != "paid" or not run.get("pay_date"):
            continue
        if period >= this_month and run["pay_date"][:7] < period:
            names = [e["name"] for e in run.get("employees", [])]
            if period == this_month:
                prepaid |= set(names)
            warnings.append(Warning("medium", "out", "early_payroll", "Payroll", f"{run['name']} was paid early, on {date.fromisoformat(run['pay_date']):%d %b}", f"£{float(run.get('total_net_pay', 0)):,.2f} net pay for {len(names)} people went out in {date.fromisoformat(run['pay_date']):%B}, the month before its period, so {date.fromisoformat(run['pay_date']):%B} carried two salary runs. Nothing is due on the 25th for these people this month; check this was intended (cash timing and payslip dates).", run["pay_date"], float(run.get("total_net_pay", 0)), float(run.get("total_net_pay", 0)), None, "high", []))

    current = [r for r in rows if _m(r["date"]) == this_month and r.get("status") not in NON_SETTLING and not r.get("internal_transfer")]
    seen_names_before = {p.name for p in patterns.values()} | {r.get("participant") for r in rows if _m(r["date"]) < this_month}

    # 1. expected payments: missing this cycle, or due within the look-ahead
    for key, p in patterns.items():
        if p.name in prepaid:
            continue
        due_month = _add_months(p.months[-1], p.cadence_months)
        paid_this_cycle = [t for t in p.tx if _m(t["date"]) >= due_month]
        if due_month > this_month:
            continue
        expected = _day_in(due_month, p.usual_day) if due_month == this_month else _day_in(due_month, p.usual_day)
        cadence_txt = "every month" if p.cadence_months == 1 else f"every {p.cadence_months} months"
        if paid_this_cycle:
            # paid: check a short payment from a customer
            got = sum(float(t["reporting_amount"]) for t in paid_this_cycle)
            if p.direction == "in" and got < 0.8 * p.typical_amount:
                warnings.append(Warning("medium", "in", "short_payment", p.name, f"{p.name} paid less than usual", f"Paid £{got:,.2f} against a usual £{p.typical_amount:,.2f} ({cadence_txt}). Check for a part payment or a disputed line.", expected.isoformat(), p.typical_amount, got, None, p.confidence, [t["id"] for t in paid_this_cycle]))
            continue
        late = (as_of - expected).days
        grace = grace_in if p.direction == "in" else grace_out
        if late > grace:
            if p.direction == "in":
                sev = "high" if p.confidence == "high" else "medium"
                warnings.append(Warning(sev, "in", "missing_receipt", p.name, f"{p.name} has not paid", f"Usually pays about £{p.typical_amount:,.2f} by {p.method} around day {p.usual_day}, {cadence_txt} (seen {len(p.months)} times). Expected {expected:%d %b}; {late} days late. Chase before it slips further.", expected.isoformat(), p.typical_amount, 0.0, late, p.confidence, []))
            else:
                ob = bills_for(p.name)
                future = [b for b in ob if b["dates"]["due_date"] > as_of.isoformat()]
                overdue = [b for b in ob if b["dates"]["due_date"] < as_of.isoformat()]
                due_today = [b for b in ob if b["dates"]["due_date"] == as_of.isoformat()]
                new_same_cat = sorted({r.get("participant") for r in current if r["direction"] == "debit" and r.get("category") == p.category and r.get("participant") not in seen_names_before})
                if overdue:
                    sev, extra = "high", f" {len(overdue)} open bill(s) from this supplier are already past due (£{sum(float(b['amounts']['total']) for b in overdue):,.2f}): the supplier will chase."
                elif due_today:
                    sev, extra = "medium", f" Bill {due_today[0].get('reference')} (£{float(due_today[0]['amounts']['total']):,.2f}) is due today." + (f" A new payee in the same category appeared this month ({', '.join(new_same_cat)}): check whether it replaced this one." if new_same_cat else "")
                elif future:
                    sev, extra = "info", f" Its next open bill ({future[0].get('reference')}) is not due until {date.fromisoformat(future[0]['dates']['due_date']):%d %b}, so this is probably timing."
                elif new_same_cat:
                    sev, extra = "medium", f" A new payee in the same category appeared this month ({', '.join(new_same_cat)}): check whether it replaced this one."
                else:
                    sev, extra = ("high" if p.method == "direct debit" else "medium"), (" A missing direct debit can mean a cancelled mandate or a failed collection." if p.method == "direct debit" else "")
                warnings.append(Warning(sev, "out", "missing_payment", p.name, f"Expected payment to {p.name} has not gone out", f"Usually £{p.typical_amount:,.2f} by {p.method} around day {p.usual_day}, {cadence_txt}. Expected {expected:%d %b}; {late} days late.{extra}", expected.isoformat(), p.typical_amount, 0.0, late, p.confidence, []))
        elif expected > as_of - timedelta(days=grace + 1) and expected <= as_of + timedelta(days=lookahead_days):
            if p.direction == "in":
                when = "today" if expected == as_of else (f"on {expected:%d %b}, not seen yet" if expected < as_of else expected.strftime("%d %b"))
                warnings.append(Warning("info", "in", "expected_receipt", p.name, f"{p.name} due to pay {when}", f"Usually about £{p.typical_amount:,.2f} by {p.method}, {cadence_txt}.", expected.isoformat(), p.typical_amount, None, None, p.confidence, []))
            elif p.typical_amount >= 2000:
                when = "due today" if expected == as_of else (f"was due {expected:%d %b}, not seen yet" if expected < as_of else f"due {expected:%d %b}")
                warnings.append(Warning("info", "out", "upcoming_payment", p.name, f"£{p.typical_amount:,.0f} to {p.name} {when}", f"Usually paid by {p.method} around day {p.usual_day}, {cadence_txt}. Make sure the paying account is funded.", expected.isoformat(), p.typical_amount, None, None, p.confidence, []))

    # 2. duplicates: same payee, same amount, within 3 days, recent
    recent_from = (as_of - timedelta(days=60)).isoformat()
    by_payee: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["direction"] == "debit" and r["date"] >= recent_from and r.get("status") not in NON_SETTLING:
            by_payee[r.get("participant") or r.get("description")].append(r)
    for name, txs in by_payee.items():
        txs.sort(key=lambda t: t["date"])
        used: set[str] = set()
        for i, a in enumerate(txs):
            for b in txs[i + 1:]:
                if b["id"] in used or a["id"] in used:
                    continue
                gap = (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days
                if gap > 3:
                    break
                if abs(float(a["reporting_amount"]) - float(b["reporting_amount"])) < 0.01:
                    used |= {a["id"], b["id"]}
                    warnings.append(Warning("high", "out", "duplicate_payment", name, f"{name} charged twice: £{float(a['reporting_amount']):,.2f}", f"Two identical {METHOD.get(a['type'], a['type'].lower())} payments {gap} day(s) apart ({date.fromisoformat(a['date']):%d %b} and {date.fromisoformat(b['date']):%d %b}). Ask for a refund of one unless both are genuine.", b["date"], float(a["reporting_amount"]), float(a["reporting_amount"]) * 2, None, "high", [a["id"], b["id"]]))

    # 3. unusually large or new-payee outgoings in the last 45 days
    big_from = (as_of - timedelta(days=45)).isoformat()
    for r in rows:
        if r["direction"] != "debit" or r["date"] < big_from or r.get("status") in NON_SETTLING:
            continue
        name = r.get("participant") or r.get("description")
        amt = float(r["reporting_amount"])
        prior = [float(t["reporting_amount"]) for t in rows if (t.get("participant") or t.get("description")) == name and t["direction"] == "debit" and t["date"] < r["date"][:8] + "01" and t["id"] != r["id"]]
        if len(prior) >= 3:
            med = statistics.median(prior)
            if amt > 1.5 * med and amt - med > 500:
                warnings.append(Warning("medium", "out", "unusual_amount", name, f"{name}: £{amt:,.2f}, {amt / med:.1f}x the usual", f"Usual single payment is about £{med:,.2f} ({len(prior)} earlier payments). Paid {date.fromisoformat(r['date']):%d %b} by {METHOD.get(r['type'], r['type'].lower())}. Check the invoice for usage spikes or a pricing change.", r["date"], round(med, 2), amt, None, "high" if len(prior) >= 5 else "medium", [r["id"]]))
        elif not prior and amt >= big_new_payee and name not in seen_names_before - {name}:
            earlier_any = [t for t in rows if (t.get("participant") or t.get("description")) == name and t["date"] < r["date"]]
            if not earlier_any:
                warnings.append(Warning("medium", "out", "new_payee", name, f"New payee: £{amt:,.2f} to {name}", f"First payment to this payee, by {METHOD.get(r['type'], r['type'].lower())} on {date.fromisoformat(r['date']):%d %b} ({r.get('category') or 'uncategorised'}). Confirm the bank details were verified and the payment approved.", r["date"], None, amt, None, "high", [r["id"]]))

    # 4. one-off receipts that should not be counted on
    for r in rows:
        if r["direction"] != "credit" or r["date"] < big_from:
            continue
        name = r.get("participant")
        if not any((t.get("participant") == name and t["date"] < r["date"]) for t in rows) and float(r["reporting_amount"]) >= big_new_payee:
            warnings.append(Warning("info", "in", "one_off_receipt", name, f"One-off receipt: £{float(r['reporting_amount']):,.2f} from {name}", f"First payment from this customer ({date.fromisoformat(r['date']):%d %b}). It lifts that month's revenue but has no pattern yet, so the forecast does not count on it repeating.", r["date"], None, float(r["reporting_amount"]), None, "medium", [r["id"]]))

    # 5. supplier bills well past due
    for b in open_bills:
        days = (as_of - date.fromisoformat(b["dates"]["due_date"])).days
        if days > 30:
            warnings.append(Warning("high", "out", "bill_overdue", (b.get("vendor") or {}).get("name", "?"), f"Bill {b.get('reference')} is {days} days overdue", f"{(b.get('amounts') or {}).get('currency', 'GBP')} {float(b['amounts']['total']):,.2f}, due {date.fromisoformat(b['dates']['due_date']):%d %b}. Pay or agree a date before it affects the supplier relationship.", b["dates"]["due_date"], float(b["amounts"]["total"]), None, days, "high", [b["id"]]))

    warnings.sort(key=lambda w: (SEVERITY_ORDER[w.severity], w.direction, -(w.days_late or 0), -(w.expected_amount or w.actual_amount or 0)))
    return warnings


def build_warnings(live: Path, as_of: date) -> list[dict]:
    rows = _read(live, "transactions_non_card.json")["data"]
    bills = _read(live, "invoices.json")["data"]
    runs = _read(live, "payroll_runs.json")["data"] if (live / "payroll_runs.json").exists() else []
    return [w.to_dict() for w in detect(rows, bills, as_of, payroll_runs=runs)]
