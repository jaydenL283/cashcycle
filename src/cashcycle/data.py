"""Shared data layer.

One place reads finance data and turns it into typed records. Agents never
touch raw JSON. Field names on the fixture are a guess at the Seapoint MCP
shape; if the live shape differs, change the `_inv`, `_card`, ... adapters
here and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


def money(value: Any) -> Decimal:
    """Parse any numeric-looking value into a Decimal with 2dp max precision kept."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    country: str
    currency: str


@dataclass(frozen=True)
class CashPosition:
    cash_balance: Decimal
    net_burn_monthly: Decimal
    as_of: date
    source: str = "fixture"


@dataclass(frozen=True)
class Invoice:
    id: str
    customer_id: str
    customer_name: str
    contact_email: str
    issue_date: date
    due_date: date
    currency: str
    amount_due: Decimal
    amount_paid: Decimal
    status: str
    key_account: bool
    disputed: bool
    contract_interest_rate: Decimal | None
    description: str

    @property
    def balance(self) -> Decimal:
        return self.amount_due - self.amount_paid


@dataclass(frozen=True)
class Card:
    id: str
    last4: str
    holder: str
    holder_status: str
    monthly_limit: Decimal
    deactivated_on: date | None = None

    @property
    def is_deactivated(self) -> bool:
        return self.holder_status.lower() == "deactivated"


@dataclass(frozen=True)
class Vendor:
    id: str
    name: str
    category: str
    billing: str
    seats: int | None = None
    active_users_90d: int | None = None
    renewal_date: date | None = None
    annual_price: Decimal | None = None
    price_per_seat: Decimal | None = None


@dataclass(frozen=True)
class Transaction:
    id: str
    date: date
    vendor_id: str | None
    description: str
    amount: Decimal  # negative = money out
    currency: str
    card_id: str | None
    category: str
    type: str

    @property
    def spend(self) -> Decimal:
        return -self.amount if self.amount < 0 else Decimal("0")


@dataclass(frozen=True)
class CustomerReply:
    """Untrusted text. Only the trust layer may read `.body`."""

    id: str
    invoice_id: str
    sender: str
    received: datetime
    body: str


@dataclass(frozen=True)
class TestDraft:
    id: str
    invoice_id: str
    step: int
    expected: str
    body: str


@dataclass(frozen=True)
class BudgetLine:
    line: str
    budget: Decimal
    actual: Decimal
    source: str


@dataclass(frozen=True)
class BurnMonth:
    month: str
    net_burn: Decimal


@dataclass(frozen=True)
class BoardPeriod:
    period: str
    currency: str
    lines: tuple[BudgetLine, ...]
    net_burn_history: tuple[BurnMonth, ...]


@dataclass
class Snapshot:
    """Everything the three agents need, loaded once."""

    company: Company
    cash: CashPosition
    invoices: list[Invoice]
    cards: list[Card]
    vendors: list[Vendor]
    transactions: list[Transaction]
    replies: list[CustomerReply]
    test_drafts: list[TestDraft]
    board: BoardPeriod | None
    as_of: date
    notes: dict[str, Any] = field(default_factory=dict)

    def invoice(self, invoice_id: str) -> Invoice:
        for inv in self.invoices:
            if inv.id == invoice_id:
                return inv
        raise KeyError(invoice_id)

    def vendor(self, vendor_id: str) -> Vendor:
        for v in self.vendors:
            if v.id == vendor_id:
                return v
        raise KeyError(vendor_id)

    def card(self, card_id: str) -> Card:
        for c in self.cards:
            if c.id == card_id:
                return c
        raise KeyError(card_id)


class DataSource(Protocol):
    def load(self) -> Snapshot: ...


# --- adapters: fixture / MCP shape -> typed records -------------------------


def _inv(d: dict[str, Any]) -> Invoice:
    rate = d.get("contract_interest_rate")
    return Invoice(
        id=d["id"],
        customer_id=d.get("customer_id", ""),
        customer_name=d["customer_name"],
        contact_email=d.get("contact_email", ""),
        issue_date=parse_date(d.get("issue_date")) or parse_date(d["due_date"]),
        due_date=parse_date(d["due_date"]),
        currency=d.get("currency", "GBP"),
        amount_due=money(d["amount_due"]),
        amount_paid=money(d.get("amount_paid", 0)),
        status=d.get("status", "open"),
        key_account=bool(d.get("key_account", False)),
        disputed=bool(d.get("disputed", False)),
        contract_interest_rate=money(rate) if rate is not None else None,
        description=d.get("description", ""),
    )


def _card(d: dict[str, Any]) -> Card:
    return Card(
        id=d["id"],
        last4=str(d.get("last4", "")),
        holder=d.get("holder", ""),
        holder_status=d.get("holder_status", "active"),
        monthly_limit=money(d.get("monthly_limit", 0)),
        deactivated_on=parse_date(d.get("deactivated_on")),
    )


def _vendor(d: dict[str, Any]) -> Vendor:
    return Vendor(
        id=d["id"],
        name=d["name"],
        category=d.get("category", ""),
        billing=d.get("billing", "monthly"),
        seats=d.get("seats"),
        active_users_90d=d.get("active_users_90d"),
        renewal_date=parse_date(d.get("renewal_date")),
        annual_price=money(d["annual_price"]) if d.get("annual_price") is not None else None,
        price_per_seat=money(d["price_per_seat"]) if d.get("price_per_seat") is not None else None,
    )


def _tx(d: dict[str, Any]) -> Transaction:
    return Transaction(
        id=d["id"],
        date=parse_date(d["date"]),
        vendor_id=d.get("vendor_id"),
        description=d.get("description", ""),
        amount=money(d["amount"]),
        currency=d.get("currency", "GBP"),
        card_id=d.get("card_id"),
        category=d.get("category", ""),
        type=d.get("type", ""),
    )


def _reply(d: dict[str, Any]) -> CustomerReply:
    return CustomerReply(
        id=d["id"],
        invoice_id=d["invoice_id"],
        sender=d.get("from", ""),
        received=datetime.fromisoformat(d["received"].replace("Z", "+00:00")),
        body=d["body"],
    )


def _draft(d: dict[str, Any]) -> TestDraft:
    return TestDraft(id=d["id"], invoice_id=d["invoice_id"], step=int(d["step"]), expected=d.get("expected", ""), body=d["body"])


def _board(d: dict[str, Any] | None) -> BoardPeriod | None:
    if not d:
        return None
    return BoardPeriod(
        period=d["period"],
        currency=d.get("currency", "GBP"),
        lines=tuple(
            BudgetLine(line=l["line"], budget=money(l["budget"]), actual=money(l["actual"]), source=l.get("source", f"ledger:{l['line'].lower()}:{d['period']}"))
            for l in d.get("lines", [])
        ),
        net_burn_history=tuple(BurnMonth(month=h["month"], net_burn=money(h["net_burn"])) for h in d.get("net_burn_history", [])),
    )


def snapshot_from_dict(raw: dict[str, Any], as_of: date | None = None) -> Snapshot:
    meta = raw.get("_meta", {})
    cb = raw["cash_and_burn"]
    as_of = as_of or parse_date(cb.get("as_of")) or parse_date(meta.get("as_of")) or date.today()
    comp = raw["company"]
    return Snapshot(
        company=Company(id=comp.get("id", ""), name=comp["name"], country=comp.get("country", "GB"), currency=comp.get("currency", "GBP")),
        cash=CashPosition(
            cash_balance=money(cb["cash_balance"]),
            net_burn_monthly=money(cb.get("net_burn_monthly_3m_avg", cb.get("net_burn_monthly"))),
            as_of=parse_date(cb.get("as_of")) or as_of,
            source=cb.get("source", "fixture"),
        ),
        invoices=[_inv(i) for i in raw.get("outstanding_invoices", []) if i.get("status", "open") == "open"],
        cards=[_card(c) for c in raw.get("cards", [])],
        vendors=[_vendor(v) for v in raw.get("vendors", [])],
        transactions=sorted((_tx(t) for t in raw.get("transactions", [])), key=lambda t: (t.date, t.id)),
        replies=[_reply(r) for r in raw.get("customer_replies", [])],
        test_drafts=[_draft(t) for t in raw.get("test_drafts", [])],
        board=_board(raw.get("board_period")),
        as_of=as_of,
        notes={"synthetic": bool(meta.get("synthetic", False)), "meta": meta},
    )


class FixtureSource:
    """Reads a JSON fixture of the same shape as the live data."""

    def __init__(self, path: str | Path, as_of: date | None = None):
        self.path = Path(path)
        self.as_of = as_of

    def load(self) -> Snapshot:
        with self.path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return snapshot_from_dict(raw, self.as_of)


def default_fixture_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "fixtures" / "fixture.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("fixtures/fixture.json not found; pass --fixture")
