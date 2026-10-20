"""Orchestration: one data layer, one trust layer, three narrow agents."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cashcycle.boardready import Commentary, Variance, compute_variances, write_commentary
from cashcycle.classifier import ReplyVerdict, classify_all
from cashcycle.collections import CollectionsTotals, RankedInvoice, allowed_numbers_for, draft_input, rank_invoices, totals
from cashcycle.config import DEFAULT_POLICY, Policy
from cashcycle.data import Snapshot
from cashcycle.leakfinder import LeakReport, find_leaks
from cashcycle.llm import LLM, LLMError
from cashcycle.runway import Runway, compute_runway, q1
from cashcycle.trust.audit import AuditLog, content_hash
from cashcycle.trust.figures import check_figures


@dataclass
class Draft:
    invoice_id: str
    step: int
    tier: str
    subject: str
    body: str
    figure_check: dict
    decision: str  # "auto_send" | "await_approval" | "blocked" | "hold_for_human" | "error"
    written_by: str
    error: str | None = None
    allowed_numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"invoice_id": self.invoice_id, "step": self.step, "tier": self.tier, "subject": self.subject, "body": self.body, "figure_check": self.figure_check, "decision": self.decision, "written_by": self.written_by, "error": self.error, "allowed_numbers": self.allowed_numbers}


def draft_for(r: RankedInvoice, llm: LLM | None, sender: str, company: str, policy: Policy = DEFAULT_POLICY) -> Draft:
    inp = draft_input(r, sender, company, policy)
    if r.step >= 4:
        return Draft(r.invoice_id, r.step, r.tier, "", "", {}, "hold_for_human", "none", "step 4 is never drafted; hand to a human", inp.allowed_numbers)
    if llm is None:
        return Draft(r.invoice_id, r.step, r.tier, "", "", {}, "error", "none", "no model configured", inp.allowed_numbers)
    try:
        out = llm.complete_json("email", json.dumps(inp.to_dict()))
    except LLMError as e:
        return Draft(r.invoice_id, r.step, r.tier, "", "", {}, "error", llm.name, str(e), inp.allowed_numbers)
    if out.get("error"):
        return Draft(r.invoice_id, r.step, r.tier, "", "", {}, "error", llm.name, out["error"], inp.allowed_numbers)
    body, subject = out.get("body", ""), out.get("subject", "")
    check = check_figures(f"{subject}\n{body}", inp.allowed_numbers, computed_interest=inp.statutory_interest_gbp, max_words=policy.max_draft_words)
    if not check.passed:
        decision = "blocked"
    elif r.reply.quarantined:
        decision = "hold_for_human"
    elif r.auto_send:
        decision = "auto_send"
    else:
        decision = "await_approval"
    return Draft(r.invoice_id, r.step, r.tier, subject, body, check.to_dict(), decision, llm.name, None, inp.allowed_numbers)


@dataclass
class CashCycleReport:
    company: str
    as_of: str
    sources: dict[str, str]
    runway: Runway
    ranked: list[RankedInvoice]
    totals: CollectionsTotals
    replies: dict[str, ReplyVerdict]
    drafts: list[Draft]
    leaks: LeakReport
    variances: list[Variance]
    commentary: Commentary | None
    hook: dict[str, Any]
    audit_head: str
    notes: list[str]

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "as_of": self.as_of,
            "sources": self.sources,
            "runway": self.runway.to_dict(),
            "invoices_ranked": [r.to_dict() for r in self.ranked],
            "totals": self.totals.to_dict(),
            "replies": {k: v.to_dict() for k, v in self.replies.items()},
            "drafts": [d.to_dict() for d in self.drafts],
            "leaks": self.leaks.to_dict(),
            "board_variances": [v.to_dict() for v in self.variances],
            "commentary": self.commentary.to_dict() if self.commentary else None,
            "hook": self.hook,
            "audit_head": self.audit_head,
            "notes": self.notes,
        }


def run_all(snap: Snapshot, llm: LLM | None, audit: AuditLog, *, sender: str = "Finance team", draft_tiers: tuple[str, ...] = ("A", "B", "C"), policy: Policy = DEFAULT_POLICY) -> CashCycleReport:
    runway = compute_runway(snap.cash, policy)
    audit.append("data", "snapshot", snap.company.id, {"as_of": snap.as_of.isoformat(), "cash_source": snap.cash.source, "cash": float(snap.cash.cash_balance), "net_burn_monthly": float(snap.cash.net_burn_monthly)})

    # Trust layer first: every reply is scanned and classified before any ranking.
    verdicts = classify_all(snap.replies, llm)
    for v in verdicts.values():
        audit.append("trust", "classify_reply", v.reply_id, {"invoice_id": v.invoice_id, "quarantine": v.quarantine, "caught_by": v.caught_by, "body_sha256": content_hash(snap_reply_body(snap, v.reply_id))})

    ranked = rank_invoices(snap, runway, {k: v.status for k, v in verdicts.items()}, policy)
    tot = totals(ranked, runway)
    for r in ranked:
        audit.append("collections", "tier", r.invoice_id, {"tier": r.tier, "step": r.step, "days_overdue": r.days_overdue, "reason": r.tier_reason, "allowed_numbers": allowed_numbers_for(r, policy)})

    drafts: list[Draft] = []
    for r in ranked:
        if r.tier not in draft_tiers:
            continue
        d = draft_for(r, llm, sender, snap.company.name, policy)
        drafts.append(d)
        audit.append("collections", f"draft_{d.decision}", r.invoice_id, {"step": d.step, "tier": d.tier, "draft_sha256": content_hash({"subject": d.subject, "body": d.body}), "figure_check": d.figure_check.get("reasons", []), "written_by": d.written_by})

    leaks = find_leaks(snap, llm, policy)
    for l in leaks.leaks:
        audit.append("leakfinder", f"leak_{l.type}", ",".join(l.vendor_ids), {"monthly_saving": float(l.monthly_saving), "evidence": l.evidence, "judgement": l.judgement})

    variances, commentary = [], None
    if snap.board:
        variances = compute_variances(snap.board, policy)
        commentary = write_commentary(snap.board, variances, runway, llm)
        audit.append("boardready", "commentary", snap.board.period, {"bullets_sha256": content_hash(commentary.bullets), "figure_check": commentary.figure_check.get("reasons", []), "written_by": commentary.written_by})

    leak_days = runway.days_for(leaks.annual_total)
    hook = {
        "overdue_gbp": float(tot.overdue_gbp),
        "overdue_runway_days": float(tot.overdue_runway_days) if tot.overdue_runway_days is not None else None,
        "leaks_monthly_gbp": float(leaks.monthly_total),
        "leaks_annual_gbp": float(leaks.annual_total),
        "leaks_annual_runway_days": float(q1(leak_days)) if leak_days is not None else None,
        "total_runway_days": float(q1(tot.overdue_runway_days + q1(leak_days))) if tot.overdue_runway_days is not None and leak_days is not None else None,
    }
    hook["line"] = f"£{tot.overdue_gbp:,.0f} overdue + £{leaks.monthly_total:,.0f}/month of quiet leaks = about {hook['total_runway_days']:.0f} days of runway" if hook["total_runway_days"] is not None else f"£{tot.overdue_gbp:,.0f} overdue + £{leaks.monthly_total:,.0f}/month of quiet leaks (runway not computable: net burn is not positive)"

    sources = snap.notes.get("sources") or {k: "fixture" for k in ("cash", "burn", "cards", "invoices", "vendors", "transactions", "replies", "board")}
    notes = list(snap.notes.get("live", {}).get("notes", []))
    if snap.notes.get("synthetic"):
        notes.append("fixture data is synthetic (Brightwell Studio Ltd is fictional)")
    return CashCycleReport(snap.company.name, snap.as_of.isoformat(), sources, runway, ranked, tot, verdicts, drafts, leaks, variances, commentary, hook, audit.head, notes)


def snap_reply_body(snap: Snapshot, reply_id: str) -> str:
    for r in snap.replies:
        if r.id == reply_id:
            return r.body
    return ""


def check_test_drafts(snap: Snapshot, runway: Runway, policy: Policy = DEFAULT_POLICY) -> list[dict]:
    """Run the figure check over the fixture's test drafts (the demo's 'wrong amount gets blocked' moment)."""
    ranked = {r.invoice_id: r for r in rank_invoices(snap, runway, {}, policy)}
    out = []
    for t in snap.test_drafts:
        r = ranked[t.invoice_id]
        # The test draft carries its own step; recompute allowed numbers for that step.
        r2 = RankedInvoice(**{**r.__dict__, "step": t.step})
        allowed = allowed_numbers_for(r2, policy)
        interest = f"{r2.statutory_interest_gbp:.2f}" if r2.statutory_interest_gbp is not None else None
        check = check_figures(t.body, allowed, computed_interest=interest, max_words=policy.max_draft_words)
        out.append({"id": t.id, "invoice_id": t.id and t.invoice_id, "step": t.step, "expected": t.expected, "result": "pass" if check.passed else "block: " + "; ".join(check.reasons), "allowed_numbers": allowed, "check": check.to_dict()})
    return out


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
