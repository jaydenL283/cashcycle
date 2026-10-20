from decimal import Decimal

from cashcycle.classifier import classify_all
from cashcycle.collections import ReplyStatus, allowed_numbers_for, draft_input, rank_invoices, totals


def _ranked(snap, runway, stub):
    verdicts = classify_all(snap.replies, stub)
    return rank_invoices(snap, runway, {k: v.status for k, v in verdicts.items()})


def test_ranking_steps_tiers_interest_match_oracle(snap, runway, stub, expected):
    ranked = _ranked(snap, runway, stub)
    assert [r.invoice_id for r in ranked] == [e["invoice_id"] for e in expected["invoices_ranked"]]
    for r, e in zip(ranked, expected["invoices_ranked"]):
        assert float(r.amount) == e["amount"], r.invoice_id
        assert r.days_overdue == e["days_overdue"], r.invoice_id
        assert r.step == e["step"], r.invoice_id
        assert r.tier == e["tier"], r.invoice_id
        assert float(r.runway_impact_days) == e["runway_impact_days"], r.invoice_id
        if "statutory_interest_to_date_gbp" in e:
            assert float(r.statutory_interest_gbp) == e["statutory_interest_to_date_gbp"], r.invoice_id
            assert float(r.fixed_compensation_gbp) == e["fixed_compensation_gbp"], r.invoice_id
        else:
            assert r.statutory_interest_gbp is None


def test_tier_a_auto_send_set(snap, runway, stub, expected):
    ranked = _ranked(snap, runway, stub)
    assert sorted(r.invoice_id for r in ranked if r.auto_send) == sorted(expected["tier_A_auto_send"])


def test_rule_order_trap_key_account_is_c(snap, runway):
    ranked = {r.invoice_id: r for r in rank_invoices(snap, runway, {})}
    r = ranked["INV-1048"]
    assert r.amount < Decimal(2000) and r.days_overdue <= 7 and not r.disputed
    assert r.tier == "C" and r.tier_reason == "key account"


def test_quarantined_reply_holds_even_when_already_c(snap, runway):
    ranked = {r.invoice_id: r for r in rank_invoices(snap, runway, {"INV-1041": ReplyStatus(quarantined=True, reply_id="REPLY-04")})}
    assert ranked["INV-1041"].tier == "C"
    assert "quarantined" in ranked["INV-1041"].tier_reason


def test_dispute_keeps_tier_b(snap, runway):
    ranked = {r.invoice_id: r for r in rank_invoices(snap, runway, {"INV-1046": ReplyStatus(is_dispute=True)})}
    assert ranked["INV-1046"].tier == "B" and not ranked["INV-1046"].auto_send


def test_totals_match_oracle(snap, runway, stub, expected):
    t = totals(_ranked(snap, runway, stub), runway)
    assert float(t.overdue_gbp) == expected["totals"]["overdue_gbp"]
    assert float(t.overdue_runway_days) == expected["totals"]["overdue_runway_days"]


def test_draft_input_has_no_customer_text_and_step3_numbers(snap, runway):
    ranked = {r.invoice_id: r for r in rank_invoices(snap, runway, {})}
    inp = draft_input(ranked["INV-1041"], "Finance", "Brightwell Studio Ltd")
    assert inp.step == 3 and inp.statutory_interest_gbp == "108.16" and inp.fixed_compensation_gbp == "70.00"
    assert "108.16" in inp.allowed_numbers and "1998" in inp.allowed_numbers
    assert "SYSTEM NOTE" not in str(inp.to_dict())
    inp1 = draft_input(ranked["INV-1043"], "Finance", "Brightwell Studio Ltd")
    assert inp1.statutory_interest_gbp is None  # only at step 3
    assert set(["1250.00", "5", "15", "10", "2026", "1043"]) <= set(allowed_numbers_for(ranked["INV-1043"]))
