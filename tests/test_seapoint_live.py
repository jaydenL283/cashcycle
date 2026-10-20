from decimal import Decimal
from pathlib import Path

from cashcycle.agents import run_all
from cashcycle.data import FixtureSource
from cashcycle.runway import compute_runway
from cashcycle.seapoint import apply_live, burn_by_month, eligible_cash, load_live, net_burn
from cashcycle.trust.audit import AuditLog

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live" / "r3con-tech"


def test_live_cash_follows_methodology():
    live = load_live(LIVE)
    # BANKING cash_position + INVESTMENT total, in reporting currency; the EUR account is converted
    assert live.cash.eligible_cash == Decimal("2459109.84")
    assert live.cash.accounts_included == 5 and live.cash.accounts_excluded_no_fx == 0


def test_live_net_burn_by_month():
    live = load_live(LIVE, "3_month_avg")
    months = {m.month: m for m in live.burn.months}
    assert months["2026-07"].debits == Decimal("124805.56")
    assert months["2026-07"].revenue_credits == Decimal("8580")
    assert months["2026-09"].net == Decimal("122503.84")
    assert live.burn.net_monthly == Decimal("120925.28")
    assert load_live(LIVE, "last_month").burn.net_monthly == Decimal("122503.84")


def test_live_overlay_runs_end_to_end(tmp_path):
    snap = apply_live(FixtureSource(ROOT / "fixtures" / "fixture.json").load(), load_live(LIVE))
    assert snap.cash.source == "seapoint_mcp"
    rw = compute_runway(snap.cash)
    assert rw.runway_days == Decimal("602.2")  # 2,459,109.84 / (122,503.84 / 30)
    rep = run_all(snap, None, AuditLog(tmp_path / "l.jsonl", tmp_path / "h"))
    assert rep.sources["cash"] == "seapoint_mcp" and rep.sources["invoices"] == "fixture"
    assert any(c.holder_status == "deactivated" for c in snap.cards)


def test_eligible_cash_excludes_accounts_without_fx():
    rows = [
        {"type": "BANKING", "reporting_balance": {"currency": "GBP", "cash_position": 100}},
        {"type": "BANKING", "balance": {"cash_position": 999}},
        {"type": "INVESTMENT", "reporting_balance": {"currency": "GBP", "total": 50}},
    ]
    c = eligible_cash(rows)
    assert c.eligible_cash == Decimal("150") and c.accounts_excluded_no_fx == 1


def test_refund_credits_reduce_burn():
    groups = [
        {"metrics": {"total": 1000}, "group": {"date": "2026-09-01", "direction": "debit", "type": "CARD_PAYMENT"}},
        {"metrics": {"total": 100}, "group": {"date": "2026-09-01", "direction": "credit", "type": "CARD_REFUND"}},
        {"metrics": {"total": 300}, "group": {"date": "2026-09-01", "direction": "credit", "type": "PAYMENT"}},
    ]
    m = burn_by_month(groups)[0]
    assert m.gross == Decimal("900") and m.net == Decimal("600")
    assert net_burn([m]).net_monthly == Decimal("600")
