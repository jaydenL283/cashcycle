from datetime import date

import pytest

from cashcycle.cli import main
from cashcycle.invoice_ref import InvalidReference, InvoiceRef, find_references, match_payment, next_ref, parse
from cashcycle.trust.figures import check_figures


def test_format_and_parse_round_trip():
    r = InvoiceRef("CS", "SRG", 2026, 10, 1)
    assert r.dashed() == "CS-SRG-2610-001" and r.compact() == "CSSRG2610001" and r.period == "2026-10"
    for text in ("CS-SRG-2610-001", "CSSRG2610001", "cs srg 2610 001", "cs/srg/2610/001"):
        assert parse(text, prefix="CS") == r
    assert parse("CSINV2610001") == InvoiceRef("CS", "INV", 2026, 10, 1)
    assert parse("CS2610001") == InvoiceRef("CS", None, 2026, 10, 1)


def test_ambiguous_five_digit_block_is_rejected():
    with pytest.raises(InvalidReference):
        parse("CSINV26101", prefix="CS")  # 2026-10 #1 or 2026-01 #01? fixed widths avoid the question
    with pytest.raises(InvalidReference):
        InvoiceRef("CS", "SRG", 2026, 13, 1)


def test_next_sequence_runs_per_month():
    issued = ["CS-SRG-2610-001", "CS-ACM-2610-002", "CS-SRG-2609-007", "INV-1041"]
    assert next_ref(issued, "CS", "BET", date(2026, 10, 20)).dashed() == "CS-BET-2610-003"
    assert next_ref(issued, "CS", "SRG", date(2026, 11, 2)).dashed() == "CS-SRG-2611-001"


def test_find_references_in_bank_text_and_match_open_invoices():
    text = "FPS PAYMENT cs srg 2610 001 and CSACM2610002 thanks"
    found = find_references(text, prefix="CS")
    assert [f.invoice_ref.dashed() for f in found] == ["CS-SRG-2610-001", "CS-ACM-2610-002"]
    open_invoices = {"inv_a": "CS-SRG-2610-001", "inv_b": "CS-ACM-2610-002", "inv_c": "CS-ACM-2610-003", "inv_old": "INV-1041"}
    hits = match_payment(text, open_invoices, prefix="CS")
    assert [h[0] for h in hits] == ["inv_a", "inv_b"]


def test_figure_check_ignores_smart_reference_digits():
    allowed = ["1250.00", "5"]
    assert check_figures("Invoice CS-SRG-2610-001 for £1,250.00 is 5 days overdue.", allowed).passed
    assert check_figures("Invoice CSSRG2610001 for £1,250.00 is 5 days overdue.", allowed).passed
    assert check_figures("Invoice INV-1043 for £1,250.00 is 5 days overdue.", allowed).passed
    assert not check_figures("Invoice CS-SRG-2610-001 for £1,520.00 is 5 days overdue.", allowed).passed


def test_cli(capsys):
    assert main(["ref", "make", "--customer", "SRG", "--date", "2026-10-20", "--existing", "CS-ACM-2610-004"]) == 0
    assert "CS-SRG-2610-005" in capsys.readouterr().out
    assert main(["ref", "parse", "cssrg2610005", "--prefix", "CS"]) == 0
    assert main(["ref", "match", "paid ref CS SRG 2610 005"]) == 0
    assert main(["ref", "parse", "CSINV26101"]) == 1
