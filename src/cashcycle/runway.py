"""Runway maths. Code only; no model anywhere near these numbers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from cashcycle.config import DEFAULT_POLICY, Policy
from cashcycle.data import CashPosition


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q1(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def pct1(x: Decimal) -> Decimal:
    """Percentages round half-to-even, matching the reporting convention (-36.25 -> -36.2)."""
    return x.quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class Runway:
    cash: Decimal
    monthly_net_burn: Decimal
    daily_net_burn: Decimal
    runway_days: Decimal | None  # None when burn <= 0 (not burning cash)

    def days_for(self, amount: Decimal) -> Decimal | None:
        """How many days of runway an amount of cash represents."""
        if self.daily_net_burn <= 0:
            return None
        return q2(amount / self.daily_net_burn)

    def to_dict(self) -> dict:
        return {
            "cash": float(self.cash),
            "monthly_net_burn": float(self.monthly_net_burn),
            "daily_net_burn": float(self.daily_net_burn),
            "runway_days": float(self.runway_days) if self.runway_days is not None else None,
        }


def compute_runway(cash: CashPosition, policy: Policy = DEFAULT_POLICY) -> Runway:
    daily = q2(cash.net_burn_monthly / Decimal(policy.days_per_month))
    if daily <= 0:
        return Runway(cash.cash_balance, cash.net_burn_monthly, daily, None)
    return Runway(cash.cash_balance, cash.net_burn_monthly, daily, q1(cash.cash_balance / daily))
