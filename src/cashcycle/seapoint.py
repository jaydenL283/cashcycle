"""Live Seapoint data, in the shape the MCP returns it.

The MCP is called by Claude (see .claude/agents/cashcycle.md), which saves each
response to data/live/<org>/. This module reads those files and applies the
Seapoint runway methodology (seapoint://finance/methodology/runway):

  numerator   = sum of reporting_balance.cash_position (BANKING) + reporting_balance.total (INVESTMENT)
  denominator = net burn: debits - refund credits - revenue credits, per full month,
                on the user's saved basis (last_month or 3_month_avg)

What the MCP does not expose (customer invoices, vendor seat usage, replies,
budget lines) is overlaid from the fixture and labelled as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from cashcycle.data import Card, CashPosition, Company, Snapshot, money, parse_date
from cashcycle.runway import q2

REFUND_TYPES = {"REFUND", "CARD_REFUND", "CARD_CHARGEBACK"}


@dataclass
class LiveCash:
    eligible_cash: Decimal
    currency: str
    accounts_included: int
    accounts_excluded_no_fx: int
    card_liability_note: bool
    source: str = "seapoint_mcp:query_accounts"

    def to_dict(self) -> dict:
        return {"eligible_cash": float(self.eligible_cash), "currency": self.currency, "accounts_included": self.accounts_included, "accounts_excluded_no_fx": self.accounts_excluded_no_fx, "card_liability_note": self.card_liability_note, "source": self.source}


def eligible_cash(accounts: list[dict]) -> LiveCash:
    total, included, excluded, cards = Decimal("0"), 0, 0, False
    currency = "GBP"
    for a in accounts:
        rb = a.get("reporting_balance")
        if not rb:
            excluded += 1
            continue
        currency = rb.get("currency", currency)
        if a.get("type") == "INVESTMENT":
            total += money(rb.get("total", 0))
        else:
            cp = rb.get("cash_position")
            if cp == 0 and a.get("institution", {}).get("name", "").lower() not in ("seapoint",):
                cards = True  # 0 may hide an unavailable card liability
            total += money(cp if cp is not None else rb.get("available", 0))
        included += 1
    return LiveCash(q2(total), currency, included, excluded, cards)


@dataclass
class MonthBurn:
    month: str
    debits: Decimal
    refund_credits: Decimal
    revenue_credits: Decimal
    complete: bool

    @property
    def net(self) -> Decimal:
        return self.debits - self.refund_credits - self.revenue_credits

    @property
    def gross(self) -> Decimal:
        return self.debits - self.refund_credits

    def to_dict(self) -> dict:
        return {"month": self.month, "debits": float(self.debits), "refund_credits": float(self.refund_credits), "revenue_credits": float(self.revenue_credits), "net_burn": float(self.net), "gross_burn": float(self.gross), "complete": self.complete}


def burn_by_month(groups: list[dict]) -> list[MonthBurn]:
    months: dict[str, MonthBurn] = {}
    for g in groups:
        grp, m = g["group"], g["group"]["date"][:7]
        mb = months.setdefault(m, MonthBurn(m, Decimal("0"), Decimal("0"), Decimal("0"), True))
        amt = money(g["metrics"]["total"])
        if grp.get("direction") == "debit":
            mb.debits += amt
        elif grp.get("type") in REFUND_TYPES:
            mb.refund_credits += amt
        else:
            mb.revenue_credits += amt
        if not g.get("money", {}).get("total", {}).get("complete", True):
            mb.complete = False
    return [months[k] for k in sorted(months)]


@dataclass
class LiveBurn:
    basis: str
    months: list[MonthBurn]
    net_monthly: Decimal
    complete: bool
    source: str = "seapoint_mcp:query_transactions(aggregate)"

    def to_dict(self) -> dict:
        return {"basis": self.basis, "months": [m.to_dict() for m in self.months], "net_monthly": float(self.net_monthly), "complete": self.complete, "source": self.source}


def net_burn(months: list[MonthBurn], basis: str = "last_month") -> LiveBurn:
    if not months:
        raise ValueError("no burn months")
    if basis == "3_month_avg":
        use = months[-3:]
        net = q2(sum((m.net for m in use), Decimal("0")) / Decimal(len(use)))
    else:
        use = months[-1:]
        net = q2(use[0].net)
    return LiveBurn(basis, months, net, all(m.complete for m in use))


def live_cards(rows: list[dict]) -> list[Card]:
    """Map the MCP card row onto the shared Card record. No deactivation date is exposed."""
    out = []
    for c in rows:
        status = c.get("status", "active")
        out.append(
            Card(
                id=c["id"],
                last4=str(c.get("last_four", "")),
                holder=c.get("holder", {}).get("name", c.get("name", "")),
                holder_status="deactivated" if status in ("cancelled", "frozen", "expired") else status,
                monthly_limit=money(c.get("limit", 0)),
                deactivated_on=None,
            )
        )
    return out


@dataclass
class LiveOverlay:
    cash: LiveCash
    burn: LiveBurn
    cards: list[Card]
    organisation_id: str | None
    fetched_at: str | None
    organisation_name: str | None = None
    notes: list[str] = field(default_factory=list)
    raw_cards: list[dict] = field(default_factory=list)
    raw_invoices: list[dict] = field(default_factory=list)
    raw_card_activity: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"cash": self.cash.to_dict(), "burn": self.burn.to_dict(), "cards": [c.id for c in self.cards], "organisation_id": self.organisation_id, "fetched_at": self.fetched_at, "notes": self.notes}


def load_live(dir_path: Path, basis: str = "last_month") -> LiveOverlay:
    def read(name: str) -> dict:
        p = dir_path / name
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)

    acc = read("accounts.json")
    burn = read("burn_by_month.json")
    cards = read("cards.json") if (dir_path / "cards.json").exists() else {"data": []}
    invoices = read("invoices.json") if (dir_path / "invoices.json").exists() else {"data": []}
    activity = read("card_activity.json") if (dir_path / "card_activity.json").exists() else {"data": []}
    cash = eligible_cash(acc["data"])
    b = net_burn(burn_by_month(burn["data"]), basis)
    notes = [f"cash and burn from Seapoint MCP export fetched {acc.get('fetched_at')}", f"burn basis {basis}; denominator is net burn per the Seapoint methodology"]
    if cash.accounts_excluded_no_fx:
        notes.append(f"{cash.accounts_excluded_no_fx} account(s) had no FX rate and are excluded from cash")
    if cash.card_liability_note:
        notes.append("a connected card reported 0 liability; missing card debt may overstate cash")
    if not b.complete:
        notes.append("a burn month had transactions with no FX rate; burn is under-counted")
    return LiveOverlay(cash, b, live_cards(cards["data"]), acc.get("organisation_id"), acc.get("fetched_at"), acc.get("organisation_name"), notes, cards["data"], invoices["data"], activity["data"])


def apply_live(snap: Snapshot, live: LiveOverlay) -> Snapshot:
    """Replace cash and burn (and cards, if present) with live values; everything else stays fixture."""
    snap.cash = CashPosition(cash_balance=live.cash.eligible_cash, net_burn_monthly=live.burn.net_monthly, as_of=parse_date(live.fetched_at) or snap.as_of, source="seapoint_mcp")
    if live.cards:
        snap.cards = live.cards
    if live.organisation_name:
        snap.company = Company(id=live.organisation_id or snap.company.id, name=f"{live.organisation_name} (receivables, vendors and board lines: sample data)", country=snap.company.country, currency=live.cash.currency)
    snap.notes["live"] = live.to_dict()
    snap.notes["live_raw"] = {"cards": live.raw_cards, "invoices": live.raw_invoices, "card_activity": live.raw_card_activity}
    snap.notes["sources"] = {"cash": "live", "burn": "live", "cards": "live" if live.cards else "sample data", "supplier_bills": "live" if live.raw_invoices else "n/a", "invoices": "sample data", "vendors": "sample data", "transactions": "sample data", "replies": "sample data", "board": "sample data"}
    return snap
