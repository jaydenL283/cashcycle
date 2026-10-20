from datetime import date
from decimal import Decimal

from cashcycle.data import CashPosition
from cashcycle.runway import compute_runway


def test_runway_matches_oracle(runway, expected):
    assert float(runway.cash) == expected["runway"]["cash"]
    assert float(runway.daily_net_burn) == expected["runway"]["daily_net_burn"]
    assert float(runway.runway_days) == expected["runway"]["runway_days"]


def test_zero_or_negative_burn_does_not_divide():
    for burn in (0, -5000):
        rw = compute_runway(CashPosition(Decimal(100000), Decimal(burn), date(2026, 10, 20)))
        assert rw.runway_days is None
        assert rw.days_for(Decimal(1000)) is None
