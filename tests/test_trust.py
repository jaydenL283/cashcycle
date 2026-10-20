import json
from pathlib import Path

from cashcycle.agents import check_test_drafts
from cashcycle.trust.audit import AuditLog, AuditRecord
from cashcycle.trust.figures import check_figures
from cashcycle.trust.scan import pattern_scan


def test_fixture_drafts_match_oracle(snap, runway, expected):
    rows = {r["id"]: r for r in check_test_drafts(snap, runway)}
    for draft_id, exp in expected["drafts"].items():
        got = rows[draft_id]["result"]
        assert got.startswith("pass") == exp.startswith("pass"), (draft_id, got)
    assert "(1520)" in rows["DRAFT-BAD-WRONG-AMOUNT"]["result"]
    assert "words" in rows["DRAFT-BAD-WORDS"]["result"]
    assert "interest" in rows["DRAFT-BAD-INTEREST"]["result"] and "108.16" in rows["DRAFT-BAD-INTEREST"]["result"]


def test_figure_check_blocks_links_and_bank_details():
    assert not check_figures("Pay £10.00 at https://example.com", ["10.00"]).passed
    assert not check_figures("Sort code 12-34-56, account number 12345678", ["10.00"]).passed
    assert check_figures("Invoice INV-1043 for £1,250.00 is 5 days overdue.", ["1250.00", "5", "1043"]).passed


def test_pattern_scan_catches_obvious_and_not_subtle(snap):
    bodies = {r.id: r.body for r in snap.replies}
    assert pattern_scan(bodies["REPLY-03"]).suspicious
    assert not pattern_scan(bodies["REPLY-04"]).suspicious  # only the classifier catches this one
    assert not pattern_scan(bodies["REPLY-01"]).suspicious
    assert not pattern_scan(bodies["REPLY-02"]).suspicious


def test_audit_chain_detects_tampering(tmp_path: Path):
    log = AuditLog(tmp_path / "log.jsonl", tmp_path / "head")
    log.append("collections", "tier", "INV-1", {"tier": "A"})
    log.append("collections", "draft_auto_send", "INV-1", {"draft_sha256": "abc"})
    pinned = log.head
    assert AuditLog(tmp_path / "log.jsonl", tmp_path / "head").verify(pinned) == (True, [])

    # Edit a record and recompute every hash: the chain is internally consistent but the head moved.
    lines = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    lines[0]["detail"]["tier"] = "C"
    prev = "0" * 64
    for d in lines:
        d["prev_hash"] = prev
        d["hash"] = AuditRecord.compute_hash(d["seq"], d["ts"], d["agent"], d["action"], d["subject"], d["detail"], prev)
        prev = d["hash"]
    (tmp_path / "log.jsonl").write_text("\n".join(json.dumps(d) for d in lines) + "\n")
    ok, problems = AuditLog(tmp_path / "log.jsonl").verify(pinned)
    assert not ok and any("pinned head" in p for p in problems)

    # Edit without recomputing: caught by the content hash.
    lines[1]["detail"]["draft_sha256"] = "zzz"
    (tmp_path / "log.jsonl").write_text("\n".join(json.dumps(d) for d in lines) + "\n")
    ok, problems = AuditLog(tmp_path / "log.jsonl").verify()
    assert not ok and any("hash does not match content" in p for p in problems)
