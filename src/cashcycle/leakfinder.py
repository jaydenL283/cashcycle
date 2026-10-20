"""Leakfinder agent: quiet spend leaks, each with its evidence.

Everything numeric is computed here. The one judgement call ("do these two
tools do the same job?") goes to the model via prompt 04 and is marked as a
judgement; nothing is cancelled automatically.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from cashcycle.config import DEFAULT_POLICY, Policy
from cashcycle.data import Snapshot, Transaction, Vendor
from cashcycle.llm import LLM, LLMError
from cashcycle.runway import q2


@dataclass
class Leak:
    type: str
    vendor_ids: list[str]
    vendor_names: list[str]
    monthly_saving: Decimal
    evidence: str
    transactions: list[str] = field(default_factory=list)
    judgement: dict | None = None
    card_id: str | None = None
    renewal_date: date | None = None
    days_until: int | None = None
    source: str = "sample data"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "vendor_ids": self.vendor_ids,
            "vendor_names": self.vendor_names,
            "monthly_saving": float(self.monthly_saving),
            "evidence": self.evidence,
            "transactions": self.transactions,
            "judgement": self.judgement,
            "card_id": self.card_id,
            "renewal_date": self.renewal_date.isoformat() if self.renewal_date else None,
            "days_until": self.days_until,
            "source": self.source,
        }


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def monthly_charges(snap: Snapshot, vendor_id: str) -> list[tuple[str, Decimal, Transaction]]:
    """(month, spend, tx) for each money-out transaction of a vendor, oldest first."""
    rows = [(_month(t.date), t.spend, t) for t in snap.transactions if t.vendor_id == vendor_id and t.spend > 0]
    return rows


def latest_monthly_cost(snap: Snapshot, vendor: Vendor) -> Decimal:
    rows = monthly_charges(snap, vendor.id)
    if not rows:
        return Decimal("0")
    if vendor.billing == "annual":
        return q2(rows[-1][1] / Decimal(12))
    return rows[-1][1]


def find_orphaned(snap: Snapshot) -> list[Leak]:
    leaks: list[Leak] = []
    for card in snap.cards:
        if not card.is_deactivated:
            continue
        cutoff = card.deactivated_on
        by_vendor: dict[str, list[Transaction]] = defaultdict(list)
        for t in snap.transactions:
            if t.card_id == card.id and t.spend > 0 and (cutoff is None or t.date > cutoff):
                by_vendor[t.vendor_id or t.description].append(t)
        if not by_vendor:
            continue
        vids = sorted(by_vendor)
        names, saving, txids, users = [], Decimal("0"), [], []
        for vid in vids:
            txs = by_vendor[vid]
            saving += txs[-1].spend
            txids += [t.id for t in txs]
            try:
                v = snap.vendor(vid)
                names.append(v.name)
                users.append(f"{v.name}: {v.active_users_90d if v.active_users_90d is not None else 'unknown'} active users")
            except KeyError:
                names.append(vid)
        months = sorted({_month(t.date) for txs in by_vendor.values() for t in txs})
        when = f"deactivated {cutoff.isoformat()}" if cutoff else f"status {card.holder_status}, date unknown"
        leaks.append(
            Leak(
                type="orphaned",
                vendor_ids=vids,
                vendor_names=names,
                monthly_saving=saving,
                evidence=f"card {card.id} ({card.holder}) {when}; charges continue {months[0]} to {months[-1]}; {'; '.join(users)}",
                transactions=txids,
                card_id=card.id,
            )
        )
    return leaks


def find_duplicates(snap: Snapshot, llm: LLM | None) -> list[Leak]:
    leaks: list[Leak] = []
    by_cat: dict[str, list[Vendor]] = defaultdict(list)
    for v in snap.vendors:
        if v.category and v.category.lower() != "rent":
            by_cat[v.category.strip().lower()].append(v)
    for vendors in by_cat.values():
        if len(vendors) < 2:
            continue
        vendors = sorted(vendors, key=lambda v: -(v.active_users_90d or 0))
        keep, others = vendors[0], vendors[1:]
        for other in others:
            judgement = _judge_duplicate(keep, other, snap, llm)
            if judgement.get("duplicate") == "no":
                continue
            saving = latest_monthly_cost(snap, other) if judgement.get("duplicate") == "yes" else Decimal("0")
            leaks.append(
                Leak(
                    type="duplicate",
                    vendor_ids=[keep.id, other.id],
                    vendor_names=[keep.name, other.name],
                    monthly_saving=saving,
                    evidence=f"two {keep.category.lower()} tools; {other.name} {other.active_users_90d} of {other.seats} seats active in 90 days, {keep.name} {keep.active_users_90d} of {keep.seats}",
                    transactions=[t.id for _, _, t in monthly_charges(snap, other.id)[-3:]],
                    judgement=judgement,
                )
            )
    return leaks


def _judge_duplicate(a: Vendor, b: Vendor, snap: Snapshot, llm: LLM | None) -> dict:
    payload = {
        "vendor_a": {"name": a.name, "category": a.category, "seats": a.seats, "active_users_90d": a.active_users_90d, "monthly_cost": float(latest_monthly_cost(snap, a))},
        "vendor_b": {"name": b.name, "category": b.category, "seats": b.seats, "active_users_90d": b.active_users_90d, "monthly_cost": float(latest_monthly_cost(snap, b))},
    }
    if llm is None:
        return {"duplicate": "unsure", "reason": "no judgement model configured", "keep_suggestion": None, "judged_by": "none"}
    try:
        out = llm.complete_json("duplicate", json.dumps(payload))
    except LLMError as e:
        return {"duplicate": "unsure", "reason": f"judgement unavailable: {e}", "keep_suggestion": None, "judged_by": llm.name}
    out["judged_by"] = llm.name
    return out


def find_renewals(snap: Snapshot, policy: Policy = DEFAULT_POLICY) -> list[Leak]:
    leaks: list[Leak] = []
    for v in snap.vendors:
        if v.billing != "annual" or v.renewal_date is None:
            continue
        days = (v.renewal_date - snap.as_of).days
        if 0 <= days <= policy.renewal_warning_days:
            annual = v.annual_price if v.annual_price is not None else latest_monthly_cost(snap, v) * 12
            seats = f"; {v.active_users_90d} of {v.seats} seats active" if v.seats else ""
            leaks.append(
                Leak(
                    type="upcoming_renewal",
                    vendor_ids=[v.id],
                    vendor_names=[v.name],
                    monthly_saving=q2(annual / Decimal(12)),
                    evidence=f"£{annual:,.0f} annual, renews {v.renewal_date.isoformat()} in {days} days{seats}",
                    transactions=[t.id for _, _, t in monthly_charges(snap, v.id)[-1:]],
                    renewal_date=v.renewal_date,
                    days_until=days,
                )
            )
    return leaks


def find_price_increases(snap: Snapshot) -> list[Leak]:
    leaks: list[Leak] = []
    for v in snap.vendors:
        if v.billing != "monthly":
            continue
        rows = monthly_charges(snap, v.id)
        if len(rows) < 3:
            continue
        latest = rows[-1][1]
        # baseline: the most common charge among earlier months
        earlier = [r[1] for r in rows[:-1]]
        baseline = max(set(earlier), key=earlier.count)
        if latest > baseline:
            first_month = next(m for m, amt, _ in rows if amt == latest and rows.index((m, amt, _)) >= len(rows) - len([r for r in rows if r[1] == latest]))
            pct = (latest - baseline) / baseline * 100
            leaks.append(
                Leak(
                    type="price_increase",
                    vendor_ids=[v.id],
                    vendor_names=[v.name],
                    monthly_saving=latest - baseline,
                    evidence=f"£{baseline:,.0f} -> £{latest:,.0f} from {first_month} (+{pct:.0f}%)",
                    transactions=[t.id for _, amt, t in rows if amt == latest],
                )
            )
    return leaks


def find_unused_seats(snap: Snapshot, already: set[str]) -> list[Leak]:
    leaks: list[Leak] = []
    for v in snap.vendors:
        if v.id in already or v.seats is None or v.active_users_90d is None or v.price_per_seat is None:
            continue
        unused = v.seats - v.active_users_90d
        if unused <= 0:
            continue
        leaks.append(
            Leak(
                type="unused_seats",
                vendor_ids=[v.id],
                vendor_names=[v.name],
                monthly_saving=q2(Decimal(unused) * v.price_per_seat),
                evidence=f"{v.seats} seats x £{v.price_per_seat:,.0f}, {v.active_users_90d} active -> {unused} unused",
                transactions=[t.id for _, _, t in monthly_charges(snap, v.id)[-1:]],
            )
        )
    return leaks


@dataclass
class LeakReport:
    leaks: list[Leak]
    monthly_total: Decimal
    annual_total: Decimal

    def to_dict(self) -> dict:
        return {"leaks": [l.to_dict() for l in self.leaks], "monthly_total": float(self.monthly_total), "annual_total": float(self.annual_total)}


def find_leaks(snap: Snapshot, llm: LLM | None, policy: Policy = DEFAULT_POLICY) -> LeakReport:
    leaks = find_orphaned(snap)
    leaks += find_duplicates(snap, llm)
    leaks += find_renewals(snap, policy)
    leaks += find_price_increases(snap)
    flagged = {vid for l in leaks for vid in l.vendor_ids}
    leaks += find_unused_seats(snap, flagged)
    monthly = sum((l.monthly_saving for l in leaks), Decimal("0"))
    return LeakReport(leaks, monthly, monthly * 12)
