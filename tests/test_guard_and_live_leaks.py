import json
from pathlib import Path

import pytest

from cashcycle.agents import run_all
from cashcycle.data import FixtureSource
from cashcycle.live_leaks import duplicate_bills, find_live_leaks, non_active_card_charges, vat_mismatches
from cashcycle.seapoint import apply_live, load_live
from cashcycle.seapoint_guard import GuardedSeapoint, WriteToolRefused, assert_read_only, is_read_only
from cashcycle.trust.audit import AuditLog

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "live" / "r3con-tech"


@pytest.mark.parametrize("name", ["query_accounts", "query_transactions", "query_invoices", "query_cards", "query_categories", "query_payments", "query_payroll", "query_treasury_returns", "get_current_user", "mcp__Seapoint_Staging__query_accounts", "mcp__seapoint__get_current_user"])
def test_read_tools_allowed(name):
    assert is_read_only(name)
    assert assert_read_only(name) in {"query_accounts", "query_transactions", "query_invoices", "query_cards", "query_categories", "query_payments", "query_payroll", "query_treasury_returns", "get_current_user"}


@pytest.mark.parametrize("name", ["set_transaction_category", "set_transaction_vendor", "set_transaction_coding", "update_vendor", "approve_reconciliation", "mcp__Seapoint_Staging__set_transaction_category", "mcp__Seapoint_Staging__update_vendor", "mcp__Seapoint_Staging__approve_reconciliation", "delete_card", "totally_new_tool"])
def test_write_and_unknown_tools_refused(name):
    assert not is_read_only(name)
    with pytest.raises(WriteToolRefused):
        assert_read_only(name)


def test_guarded_transport_never_sees_a_write():
    seen = []
    g = GuardedSeapoint(transport=lambda tool, args: seen.append((tool, args)) or {"ok": True})
    assert g.call("mcp__Seapoint_Staging__query_cards", {"limit": 5}) == {"ok": True}
    with pytest.raises(WriteToolRefused):
        g.call("mcp__Seapoint_Staging__set_transaction_vendor", {"transaction_id": "txn_1", "vendor": "x"})
    assert seen == [("query_cards", {"limit": 5})]
    assert g.refused == ["mcp__Seapoint_Staging__set_transaction_vendor"]


def _invoices():
    return json.loads((LIVE / "invoices.json").read_text())["data"]


def test_duplicate_bills_found():
    dups = {l.evidence.split(" ")[0]: l for l in duplicate_bills(_invoices())}
    assert set(dups) == {"SNF-INV-2026-10", "CM-2026-114"}
    assert "21,800.00" in dups["SNF-INV-2026-10"].evidence and "2 unpaid" in dups["SNF-INV-2026-10"].evidence
    assert "3,400.00" in dups["CM-2026-114"].evidence
    assert all(l.source == "live" and l.type == "duplicate_bill" for l in dups.values())
    # CM-2026-109 (paid, different reference) and SNF-INV-2026-09 are not duplicates of the above
    assert all("2026-109" not in l.evidence and "2026-09 " not in l.evidence for l in dups.values())


def test_vat_mismatch_found_only_on_remote_people():
    bad = vat_mismatches(_invoices())
    assert [l.vendor_names[0] for l in bad] == ["Remote People Ltd"]
    assert "RP-2026-10-118" in bad[0].evidence and "6,680.00 expected VAT" in bad[0].evidence and "200.00" in bad[0].evidence
    assert bad[0].source == "live"


def test_non_active_card_charges():
    cards = json.loads((LIVE / "cards.json").read_text())["data"]
    activity = json.loads((LIVE / "card_activity.json").read_text())["data"]
    found = non_active_card_charges(cards, activity)
    assert len(found) == 1  # the cancelled card has no charges; the frozen Travel card does
    l = found[0]
    assert l.card_id == "card_3gtkc2q5719q9gytyer1r0617m" and l.source == "live"
    assert "frozen" in l.evidence and "status date unknown" in l.evidence and "1,215.00" in l.evidence
    assert float(l.monthly_saving) == 1215.0


def test_live_report_labels_and_headline(tmp_path):
    snap = apply_live(FixtureSource(ROOT / "fixtures" / "fixture.json").load(), load_live(LIVE))
    rep = run_all(snap, None, AuditLog(tmp_path / "l.jsonl", tmp_path / "h"))
    assert rep.sources["invoices"] == "sample data" and rep.sources["burn"] == "live" and rep.sources["supplier_bills"] == "live"
    # live daily net burn 122,503.84 / 30 = 4,083.46; £25,000 overdue -> 6.1 days
    assert float(rep.runway.daily_net_burn) == 4083.46
    assert float(rep.totals.overdue_runway_days) == 6.1
    assert "(sample data)" in rep.hook["line"] and "live net burn of £4,083.46/day" in rep.hook["line"]
    by_type = {l.type: l for l in rep.leaks.leaks}
    assert {"duplicate_bill", "vat_mismatch", "non_active_card_charges"} <= set(by_type)
    assert all(l.source == "live" for l in rep.leaks.leaks if l.type in ("duplicate_bill", "vat_mismatch", "non_active_card_charges"))
    assert all(l.source == "sample data" for l in rep.leaks.leaks if l.type in ("duplicate", "upcoming_renewal", "price_increase", "unused_seats"))
    by_inv = {r.invoice_id: r for r in rep.ranked}
    assert float(by_inv["INV-1041"].runway_impact_days) == 2.06  # 8400 / 4083.46


def test_live_leaks_empty_inputs():
    assert find_live_leaks([], [], []) == []
