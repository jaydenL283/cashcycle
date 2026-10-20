"""Evidence for the dashboard's "How it works" page: the safety checks run for real.

Uses the sample company's test drafts and customer replies (fixtures/fixture.json,
fictional) because those are the cases built to exercise the trust layer.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cashcycle.agents import check_test_drafts, run_all
from cashcycle.classifier import classify_all
from cashcycle.data import FixtureSource, default_fixture_path
from cashcycle.llm import LLM, StubLLM
from cashcycle.runway import compute_runway
from cashcycle.seapoint_guard import WriteToolRefused, assert_read_only
from cashcycle.trust.audit import AuditLog, AuditRecord

SEAPOINT_TOOLS = [
    "get_current_user", "query_accounts", "query_transactions", "query_invoices", "query_cards", "query_categories",
    "query_payments", "query_payroll", "query_transaction_participants", "query_treasury_returns",
    "set_transaction_category", "set_transaction_vendor", "set_transaction_coding", "update_vendor", "approve_reconciliation",
]

AUTONOMY = [
    {"step": 1, "action": "Rank invoices by runway impact, set the reminder step and tier, work out late-payment interest", "who": "agent", "rule": "Always, in code. The model is not involved."},
    {"step": 2, "action": "Send a reminder email", "who": "agent", "rule": "Only Tier A: under £2,000, at most 7 days overdue, not disputed, not a key account, no flagged reply, and the figure check passed."},
    {"step": 3, "action": "Send any other reminder (larger, older, disputed)", "who": "person", "rule": "One-click approval."},
    {"step": 4, "action": "Final notices and anything with a flagged customer reply", "who": "person", "rule": "Held for a person to read and approve."},
    {"step": 5, "action": "Find leaks: orphaned cards, duplicate tools, renewals, price rises, unused seats, duplicate bills, VAT gaps", "who": "agent", "rule": "Found and shown with evidence. Nothing is cancelled automatically."},
    {"step": 6, "action": "Early warnings on payments in and out", "who": "agent", "rule": "Learnt from each payee's history and raised automatically. A person decides what to do."},
    {"step": 7, "action": "Board commentary", "who": "agent", "rule": "Written by the model from code-computed variances, blocked if any number is not on the allowed list."},
    {"step": 8, "action": "Change anything in Seapoint (categories, vendors, coding, approvals)", "who": "never", "rule": "Refused in code: only query tools can be called."},
]


def build(llm: LLM | None = None) -> dict:
    llm = llm or StubLLM()
    snap = FixtureSource(default_fixture_path()).load()
    rw = compute_runway(snap.cash)
    drafts = []
    for r in check_test_drafts(snap, rw):
        drafts.append({"id": r["id"], "invoice": r["invoice_id"], "draft": next(t.body for t in snap.test_drafts if t.id == r["id"]),
                       "result": "passed" if r["result"] == "pass" else "blocked", "reason": "" if r["result"] == "pass" else r["result"].removeprefix("block: "),
                       "matches_expected": (r["result"] == "pass") == (r["expected"] == "pass")})
    replies = []
    verdicts = classify_all(snap.replies, llm)
    for rep in snap.replies:
        v = verdicts[rep.invoice_id]
        replies.append({"id": rep.id, "invoice": rep.invoice_id, "reply": rep.body.replace("\n", " ")[:160] + ("…" if len(rep.body) > 160 else ""),
                        "outcome": "quarantined" if v.quarantine else ("clean, dispute" if v.is_dispute else "clean"),
                        "caught_by": {"pattern_scan": "pattern scan", "classifier": "classifier", "none": "", "classifier_unavailable": "held: classifier unavailable"}[v.caught_by],
                        "judged_by": "pattern scan + " + ("Claude classifier" if llm.name.startswith("claude") else "offline stand-in for the Claude classifier")})
    guard = []
    for t in SEAPOINT_TOOLS:
        try:
            assert_read_only(t)
            guard.append({"tool": t, "result": "allowed", "why": "read-only"})
        except WriteToolRefused:
            guard.append({"tool": t, "result": "refused", "why": "can change data"})
    with tempfile.TemporaryDirectory() as d:
        log = AuditLog(Path(d) / "log.jsonl", Path(d) / "head")
        run_all(snap, llm, log)
        ok, _ = log.verify()
        pinned = log.head
        lines = [json.loads(x) for x in (Path(d) / "log.jsonl").read_text().splitlines()]
        lines[5]["detail"]["tier"] = "A"
        prev = "0" * 64
        for x in lines:
            x["prev_hash"] = prev
            x["hash"] = AuditRecord.compute_hash(x["seq"], x["ts"], x["agent"], x["action"], x["subject"], x["detail"], prev)
            prev = x["hash"]
        (Path(d) / "log.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        tampered_ok, problems = AuditLog(Path(d) / "log.jsonl").verify(pinned)
    audit = [
        {"metric": "records", "value": len(lines), "label": "Decisions logged in one run"},
        {"metric": "verifies", "value": "yes" if ok else "no", "label": "Chain verifies against the pinned head"},
        {"metric": "head", "value": pinned[:16] + "…", "label": "Pinned head hash"},
        {"metric": "tamper_caught", "value": "yes" if not tampered_ok else "no", "label": "Edit one record and rebuild every hash: caught"},
    ]
    return {"drafts": drafts, "replies": replies, "guard": guard, "autonomy": AUTONOMY, "audit": audit}
