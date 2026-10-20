"""Collections agent: rank open invoices by runway impact, decide step and tier in code.

Nothing here reads customer text. The drafter (an LLM) only ever sees the
structured `DraftInput` built at the bottom of this file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

from cashcycle.config import DEFAULT_POLICY, Policy
from cashcycle.data import Invoice, Snapshot
from cashcycle.runway import Runway, q1, q2

TIER_A = "A"  # small, fresh, clean: may auto-send
TIER_B = "B"  # one-click human approval
TIER_C = "C"  # human writes or reviews; key accounts, final notices, flagged replies


@dataclass(frozen=True)
class ReplyStatus:
    """What the trust layer concluded about the latest customer reply on an invoice."""

    quarantined: bool = False
    is_dispute: bool = False
    reply_id: str | None = None


def days_overdue(inv: Invoice, as_of: date) -> int:
    return (as_of - inv.due_date).days


def escalation_step(days: int, policy: Policy = DEFAULT_POLICY) -> int:
    if days <= 0:
        return 0
    for lo, hi, step in policy.step_bands:
        if lo <= days <= hi:
            return step
    return policy.step_after_bands


def statutory_interest(inv: Invoice, days: int, policy: Policy = DEFAULT_POLICY) -> Decimal | None:
    """Interest to date under the Late Payment Act. None when not overdue or the contract sets its own rate."""
    if days <= 0 or inv.contract_interest_rate is not None:
        return None
    rate = policy.statutory_rate_pct / Decimal(100)
    return q2(inv.balance * rate / Decimal(365) * Decimal(days))


def fixed_compensation(inv: Invoice, days: int, policy: Policy = DEFAULT_POLICY) -> Decimal | None:
    if days <= 0 or inv.contract_interest_rate is not None:
        return None
    for upper, fee in policy.fixed_compensation_bands:
        if inv.balance < upper:
            return fee
    return policy.fixed_compensation_top


def decide_tier(inv: Invoice, days: int, step: int, reply: ReplyStatus, policy: Policy = DEFAULT_POLICY) -> tuple[str, str]:
    """Return (tier, reason). ORDER MATTERS: the hold rules come first.

    The build guide's original order checked "small and fresh" before "key
    account", which wrongly auto-sends to a key account. Key account, final
    notice and quarantined reply are checked FIRST.
    """
    if reply.quarantined:
        return TIER_C, "reply quarantined; hold for human"
    if inv.key_account:
        return TIER_C, "key account"
    if step >= 3:
        return TIER_C, "final notice or beyond"
    if inv.disputed or reply.is_dispute:
        return TIER_B, "disputed; no auto-send"
    if inv.balance < policy.tier_a_threshold and days <= policy.tier_a_max_days_overdue:
        return TIER_A, "small, at most 7 days overdue, clean"
    return TIER_B, "needs one-click approval"


@dataclass
class RankedInvoice:
    invoice_id: str
    customer_name: str
    contact_email: str
    amount: Decimal
    currency: str
    due_date: date
    days_overdue: int
    step: int
    tier: str
    tier_reason: str
    runway_impact_days: Decimal | None
    statutory_interest_gbp: Decimal | None
    fixed_compensation_gbp: Decimal | None
    key_account: bool
    disputed: bool
    reply: ReplyStatus = field(default_factory=ReplyStatus)

    @property
    def auto_send(self) -> bool:
        return self.tier == TIER_A and self.step in (0, 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, Decimal):
                d[k] = float(v)
            elif isinstance(v, date):
                d[k] = v.isoformat()
        d["auto_send"] = self.auto_send
        return d


def rank_invoices(snap: Snapshot, runway: Runway, replies: dict[str, ReplyStatus] | None = None, policy: Policy = DEFAULT_POLICY) -> list[RankedInvoice]:
    replies = replies or {}
    out: list[RankedInvoice] = []
    for inv in snap.invoices:
        if inv.balance <= 0:
            continue
        days = days_overdue(inv, snap.as_of)
        step = escalation_step(days, policy)
        rs = replies.get(inv.id, ReplyStatus())
        tier, reason = decide_tier(inv, days, step, rs, policy)
        out.append(
            RankedInvoice(
                invoice_id=inv.id,
                customer_name=inv.customer_name,
                contact_email=inv.contact_email,
                amount=inv.balance,
                currency=inv.currency,
                due_date=inv.due_date,
                days_overdue=days,
                step=step,
                tier=tier,
                tier_reason=reason,
                runway_impact_days=runway.days_for(inv.balance),
                statutory_interest_gbp=statutory_interest(inv, days, policy),
                fixed_compensation_gbp=fixed_compensation(inv, days, policy),
                key_account=inv.key_account,
                disputed=inv.disputed,
                reply=rs,
            )
        )
    # Runway impact first; age breaks ties so the older invoice of two equal amounts leads.
    out.sort(key=lambda r: (-(r.runway_impact_days or r.amount), -r.days_overdue, r.invoice_id))
    return out


@dataclass
class CollectionsTotals:
    overdue_gbp: Decimal
    overdue_runway_days: Decimal | None
    overdue_count: int

    def to_dict(self) -> dict:
        return {"overdue_gbp": float(self.overdue_gbp), "overdue_runway_days": float(self.overdue_runway_days) if self.overdue_runway_days is not None else None, "overdue_count": self.overdue_count}


def totals(ranked: list[RankedInvoice], runway: Runway) -> CollectionsTotals:
    overdue = [r for r in ranked if r.days_overdue > 0]
    total = sum((r.amount for r in overdue), Decimal("0"))
    days = runway.days_for(total)
    return CollectionsTotals(total, q1(days) if days is not None else None, len(overdue))


# --- what the drafter is allowed to see -----------------------------------


@dataclass
class DraftInput:
    """Structured, code-built input for the email drafter. No customer text."""

    invoice_id: str
    customer_name: str
    contact_first_name: str | None
    amount_due: str
    currency: str
    due_date: str
    days_overdue: int
    step: int
    sender_name: str
    company_name: str
    statutory_interest_gbp: str | None
    fixed_compensation_gbp: str | None
    allowed_numbers: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _fmt(d: Decimal) -> str:
    return f"{q2(d):.2f}"


def allowed_numbers_for(r: RankedInvoice, policy: Policy = DEFAULT_POLICY) -> list[str]:
    """Every number the drafter may write, as strings. The figure check uses the same list."""
    nums: list[str] = [_fmt(r.amount), str(r.days_overdue), str(abs(r.days_overdue)), str(r.due_date.day), str(r.due_date.month), str(r.due_date.year), str(policy.days_to_pay_in_reminder)]
    # the numeric part of the invoice id, so "INV-1043" is not a foreign number
    digits = "".join(ch for ch in r.invoice_id if ch.isdigit())
    if digits:
        nums.append(digits)
    if r.step == 3:
        nums.append(str(policy.late_payment_act_year))
        if r.statutory_interest_gbp is not None:
            nums.append(_fmt(r.statutory_interest_gbp))
        if r.fixed_compensation_gbp is not None:
            nums.append(_fmt(r.fixed_compensation_gbp))
    # keep order, drop duplicates
    seen: set[str] = set()
    return [n for n in nums if not (n in seen or seen.add(n))]


def draft_input(r: RankedInvoice, sender_name: str, company_name: str, policy: Policy = DEFAULT_POLICY) -> DraftInput:
    return DraftInput(
        invoice_id=r.invoice_id,
        customer_name=r.customer_name,
        contact_first_name=None,
        amount_due=_fmt(r.amount),
        currency=r.currency,
        due_date=r.due_date.isoformat(),
        days_overdue=r.days_overdue,
        step=r.step,
        sender_name=sender_name,
        company_name=company_name,
        statutory_interest_gbp=_fmt(r.statutory_interest_gbp) if r.step == 3 and r.statutory_interest_gbp is not None else None,
        fixed_compensation_gbp=_fmt(r.fixed_compensation_gbp) if r.step == 3 and r.fixed_compensation_gbp is not None else None,
        allowed_numbers=allowed_numbers_for(r, policy),
    )
