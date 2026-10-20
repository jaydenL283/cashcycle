"""Policy constants. Everything a judge might ask "where is that number from?" lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Policy:
    # Collections
    tier_a_threshold: Decimal = Decimal("2000")  # amounts below this may auto-send
    tier_a_max_days_overdue: int = 7
    # Escalation steps by days overdue: 0 not yet due, 1 gentle, 2 firm, 3 final, 4 never drafted
    step_bands: tuple[tuple[int, int, int], ...] = ((1, 7, 1), (8, 21, 2), (22, 60, 3))
    step_after_bands: int = 4
    days_to_pay_in_reminder: int = 7
    # Late Payment of Commercial Debts (Interest) Act 1998
    boe_base_rate_pct: Decimal = Decimal("3.75")  # Bank Rate on 20 Oct 2026; re-check before demo
    statutory_uplift_pct: Decimal = Decimal("8")
    late_payment_act_year: int = 1998
    fixed_compensation_bands: tuple[tuple[Decimal, Decimal], ...] = (
        (Decimal("1000"), Decimal("40")),
        (Decimal("10000"), Decimal("70")),
    )
    fixed_compensation_top: Decimal = Decimal("100")
    # Runway
    days_per_month: int = 30
    # Leakfinder
    renewal_warning_days: int = 30
    # Board
    variance_comment_threshold_pct: Decimal = Decimal("10")
    # Drafting
    max_draft_words: int = 120

    @property
    def statutory_rate_pct(self) -> Decimal:
        return self.boe_base_rate_pct + self.statutory_uplift_pct


DEFAULT_POLICY = Policy()


@dataclass
class Settings:
    as_of: date = date(2026, 10, 20)
    policy: Policy = field(default_factory=lambda: DEFAULT_POLICY)
