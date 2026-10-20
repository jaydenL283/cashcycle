"""Injection classifier: pattern scan first, then an isolated Claude call with no tools.

The classifier only decides whether a human looks at the reply. It never
changes an amount, a tier or a recipient; those are computed in code from
structured fields the model never sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cashcycle.collections import ReplyStatus
from cashcycle.data import CustomerReply
from cashcycle.llm import LLM, LLMError
from cashcycle.trust.scan import ScanResult, pattern_scan


@dataclass
class ReplyVerdict:
    reply_id: str
    invoice_id: str
    quarantine: bool
    is_dispute: bool
    caught_by: str  # "pattern_scan" | "classifier" | "none" | "classifier_unavailable"
    scan: ScanResult
    classifier: dict | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reply_id": self.reply_id,
            "invoice_id": self.invoice_id,
            "quarantine": self.quarantine,
            "is_dispute": self.is_dispute,
            "caught_by": self.caught_by,
            "scan": self.scan.to_dict(),
            "classifier": self.classifier,
            "reasons": self.reasons,
        }

    @property
    def status(self) -> ReplyStatus:
        return ReplyStatus(quarantined=self.quarantine, is_dispute=self.is_dispute, reply_id=self.reply_id)


def wrap_untrusted(text: str) -> str:
    # Neutralise a closing tag inside the text so it cannot escape the wrapper.
    safe = text.replace("</untrusted>", "</untrusted_>")
    return f"<untrusted>\n{safe}\n</untrusted>"


def classify_reply(reply: CustomerReply, llm: LLM | None) -> ReplyVerdict:
    scan = pattern_scan(reply.body)
    verdict: dict | None = None
    reasons: list[str] = list(scan.evidence)
    caught_by = "pattern_scan" if scan.suspicious else "none"
    is_dispute = False

    if llm is not None:
        try:
            verdict = llm.complete_json("classifier", wrap_untrusted(reply.body))
            is_dispute = bool(verdict.get("is_dispute"))
            if verdict.get("verdict") == "suspicious":
                reasons += list(verdict.get("reasons", []))
                if not scan.suspicious:
                    caught_by = "classifier"
        except LLMError as e:
            # If unsure, quarantine. An unavailable classifier must not let text through.
            reasons.append(f"classifier unavailable ({e}); held for human")
            caught_by = "classifier_unavailable"
            return ReplyVerdict(reply.id, reply.invoice_id, True, False, caught_by, scan, None, reasons)

    quarantine = scan.suspicious or (verdict is not None and verdict.get("verdict") == "suspicious")
    return ReplyVerdict(reply.id, reply.invoice_id, quarantine, is_dispute, caught_by, scan, verdict, reasons)


def classify_all(replies: list[CustomerReply], llm: LLM | None) -> dict[str, ReplyVerdict]:
    """Latest reply per invoice wins."""
    out: dict[str, ReplyVerdict] = {}
    for r in sorted(replies, key=lambda r: r.received):
        out[r.invoice_id] = classify_reply(r, llm)
    return out
