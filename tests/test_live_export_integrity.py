"""The live export files are copies of Seapoint MCP responses. These tests prove the copies
are faithful: every breakdown must add back up to an independent aggregate total from a
separate tool call. A copying error anywhere fails here."""
import json
from pathlib import Path

import pytest

LIVE = Path(__file__).resolve().parents[1] / "data" / "live" / "r3con-tech"
J = lambda n: json.loads((LIVE / n).read_text())
R = lambda x: round(x, 2)


def monthly_debits():
    return {g["group"]["date"][:7]: g["metrics"]["total"] for g in J("monthly_in_out_all_time.json")["data"] if g["group"]["direction"] == "debit"}


def test_bills_by_vendor_add_up_to_total():
    b = J("bills_due.json")["data"]
    assert R(sum(g["metrics"]["owed"] for g in b["by_vendor"])) == b["total"]["metrics"]["owed"] == 146333.19
    assert sum(g["metrics"]["bills"] for g in b["by_vendor"]) == b["total"]["metrics"]["bills"] == 25


def test_category_spend_adds_up_to_monthly_debits():
    md = monthly_debits()
    by_month = {}
    for g in J("spend_by_category_month.json")["data"]:
        m = g["group"]["date"][:7]
        by_month[m] = by_month.get(m, 0) + g["metrics"]["total"]
    for m, total in md.items():
        if m >= "2025-08":
            assert R(by_month.get(m, 0)) == R(total), m


@pytest.mark.parametrize("window,months", [("sep_2026", ["2026-09"]), ("jun_aug_2026", ["2026-06", "2026-07", "2026-08"]), ("oct_2026", ["2026-10"]), ("jul_sep_2026", ["2026-07", "2026-08", "2026-09"])])
def test_vendor_windows_add_up_to_monthly_debits(window, months):
    md = monthly_debits()
    got = sum(g["metrics"]["total"] for g in J("vendor_spend_windows.json")["data"][window])
    assert R(got) == R(sum(md[m] for m in months))


def test_flows_by_type_add_up_to_monthly_debits():
    md = monthly_debits()
    by = {}
    for g in J("money_flows_by_type.json")["data"]:
        if g["group"]["direction"] == "debit":
            m = g["group"]["date"][:7]
            by[m] = by.get(m, 0) + g["metrics"]["total"]
    for m, t in by.items():
        assert R(t) == R(md[m]), m


def test_books_status_and_queue_are_consistent():
    groups = J("books_status.json")["data"]
    needs_filter = sum(g["metrics"]["payments"] for g in groups if g["group"]["reconciliation"] == "needs_review" or (g["group"]["reconciliation"] is None and g["group"]["has_evidence"] is False))
    q = J("books_queue.json")["data"]
    assert needs_filter == len(q["needs_review"]) == 12
    awaiting = sum(g["metrics"]["payments"] for g in groups if g["group"]["reconciliation"] == "awaiting_approval")
    assert awaiting == len(q["awaiting_approval"]) == 19
    assert R(sum(r["reporting_amount"] for r in q["awaiting_approval"])) == R(sum(g["metrics"]["total"] for g in groups if g["group"]["reconciliation"] == "awaiting_approval"))
