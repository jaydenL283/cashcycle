from cashcycle.boardready import allowed_numbers_for_board, commentary_input, compute_variances, write_commentary
from cashcycle.leakfinder import find_leaks


def test_leaks_match_oracle(snap, stub, expected):
    rep = find_leaks(snap, stub)
    got = {l.type: l for l in rep.leaks}
    assert set(got) == {e["type"] for e in expected["leaks"]}
    for e in expected["leaks"]:
        l = got[e["type"]]
        assert sorted(l.vendor_ids) == sorted(e["vendor_ids"]), e["type"]
        assert float(l.monthly_saving) == e["monthly_saving"], e["type"]
        if "card_id" in e:
            assert l.card_id == e["card_id"]
        if "days_until" in e:
            assert l.days_until == e["days_until"]
    assert float(rep.monthly_total) == expected["totals"]["leaks_monthly_gbp"]
    assert float(rep.annual_total) == expected["totals"]["leaks_annual_gbp"]
    assert got["duplicate"].judgement["duplicate"] == "yes"
    assert all(l.evidence for l in rep.leaks)


def test_leaks_without_judgement_model_are_marked_unsure(snap):
    rep = find_leaks(snap, None)
    dup = next(l for l in rep.leaks if l.type == "duplicate")
    assert dup.judgement["duplicate"] == "unsure" and dup.monthly_saving == 0


def test_board_variances_match_oracle(snap, expected):
    vs = compute_variances(snap.board)
    for v, e in zip(vs, expected["board_variances"]):
        assert v.line == e["line"]
        assert float(v.variance) == e["variance"]
        assert float(v.variance_pct) == e["variance_pct"]
    by = {v.line: v for v in vs}
    assert by["Revenue"].favourable is False and by["Marketing"].favourable is True and by["Rent"].favourable is None
    assert {v.line for v in vs if v.comment_worthy} == {"Revenue", "Contractors", "Software", "Marketing"}


def test_board_commentary_passes_figure_check_with_stub(snap, runway, stub):
    vs = compute_variances(snap.board)
    c = write_commentary(snap.board, vs, runway, stub)
    assert c.error is None and 4 <= len(c.bullets) <= 5
    assert all(b.rstrip().endswith("]") for b in c.bullets)


def test_board_allowed_numbers_cover_inputs(snap, runway):
    vs = compute_variances(snap.board)
    allowed = allowed_numbers_for_board(commentary_input(snap.board, vs, runway))
    for n in ("62000", "55400", "6600", "10.6", "43460", "51740", "2026"):
        assert n in allowed
