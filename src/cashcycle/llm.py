"""The only place that talks to Claude.

Every call is a single request with JSON-schema structured output, no tools,
and a system prompt loaded from prompts/. A `StubLLM` with the same interface
runs the whole demo offline; its output is labelled as a stub, never as Claude.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_MODEL = "claude-opus-5-5"

PROMPT_FILES = {
    "email": "01_email_drafting.txt",
    "variance": "02_variance_commentary.txt",
    "classifier": "03_injection_classifier.txt",
    "duplicate": "04_duplicate_tool_judgement.txt",
}


def prompts_dir() -> Path:
    env = os.environ.get("CASHCYCLE_PROMPTS")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "prompts" / PROMPT_FILES["email"]).exists():
            return parent / "prompts"
    raise FileNotFoundError("prompts/ directory not found; set CASHCYCLE_PROMPTS")


def load_prompt(name: str) -> str:
    return (prompts_dir() / PROMPT_FILES[name]).read_text(encoding="utf-8")


# JSON schemas for structured output. They mirror the OUTPUT line of each prompt.
SCHEMAS: dict[str, dict[str, Any]] = {
    "email": {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "numbers_used": {"type": "array", "items": {"type": "string"}},
            "error": {"type": "string"},
        },
        "required": ["subject", "body", "numbers_used", "error"],
        "additionalProperties": False,
    },
    "variance": {
        "type": "object",
        "properties": {
            "bullets": {"type": "array", "items": {"type": "string"}},
            "numbers_used": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["bullets", "numbers_used"],
        "additionalProperties": False,
    },
    "classifier": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["clean", "suspicious"]},
            "is_dispute": {"type": "boolean"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "quoted_evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["verdict", "is_dispute", "reasons", "quoted_evidence"],
        "additionalProperties": False,
    },
    "duplicate": {
        "type": "object",
        "properties": {
            "duplicate": {"type": "string", "enum": ["yes", "no", "unsure"]},
            "reason": {"type": "string"},
            "keep_suggestion": {"type": ["string", "null"]},
        },
        "required": ["duplicate", "reason", "keep_suggestion"],
        "additionalProperties": False,
    },
}


class LLM(Protocol):
    name: str

    def complete_json(self, prompt_name: str, user_content: str) -> dict[str, Any]: ...


@dataclass
class ClaudeLLM:
    """Calls the Messages API with structured output. Thinking stays on; effort is per call type."""

    model: str = DEFAULT_MODEL
    effort: str = "medium"
    fallbacks: bool = True
    name: str = field(init=False)

    def __post_init__(self) -> None:
        import anthropic  # imported lazily so the offline stub needs no SDK

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.name = f"claude:{self.model}"

    def complete_json(self, prompt_name: str, user_content: str) -> dict[str, Any]:
        system = load_prompt(prompt_name)
        schema = SCHEMAS[prompt_name]
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            if self.fallbacks:
                # Server-side refusal fallback: on a safety decline the API re-runs on a fallback model.
                response = self._client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                response = self._client.messages.create(**kwargs)
        except self._anthropic.RateLimitError as e:
            raise LLMError(f"rate limited: {e.message}") from e
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError("network error reaching the Claude API") from e
        if response.stop_reason == "refusal":
            raise LLMError("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("response truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return parse_json(text)


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("model did not return JSON")
        return json.loads(m.group(0))


# --- offline stub -----------------------------------------------------------


_STUB_SUSPICIOUS = [
    (re.compile(r"\b(ai|assistant|system|bot|model|whoever processes this)\b", re.I), "addresses an AI or 'whoever processes this'"),
    (re.compile(r"\b(ignore|override|disregard)\b.{0,40}\b(instructions?|rules?|tier|status|approval)\b", re.I | re.S), "tells the reader to override instructions"),
    (re.compile(r"\b(update|change|correct|amend|reduce)\b.{0,40}\b(invoice|amount|figure|balance)\b.{0,40}\b(to|as)\b", re.I | re.S), "asks to change an amount"),
    (re.compile(r"\b(mark|treat|record)\b.{0,40}\b(paid|settled|written off)\b", re.I | re.S), "asks to mark something paid or settled"),
    (re.compile(r"\bconfirm\b.{0,30}\b(corrected|new|revised)\b.{0,20}\b(amount|figure|balance)\b", re.I | re.S), "asks to 'confirm' a new figure"),
    (re.compile(r"\b(send|forward|email|share)\b.{0,40}\b(to|with)\b.{0,40}\b(address|email|account|data)\b", re.I | re.S), "asks to send to a different address or share data"),
    (re.compile(r"\bas agreed\b.{0,40}\b(phone|call|verbally|with your)\b", re.I | re.S), "claims an agreement that a human should check"),
    (re.compile(r"#{2,}|<\s*/?\s*(system|instruction)|\[\s*system", re.I), "system-styled markup"),
]
_STUB_DISPUTE = re.compile(r"\b(not paying|dispute|disputed|was not delivered|not delivered|wrong|incorrect|query the invoice|withhold)\b", re.I)


@dataclass
class StubLLM:
    """Deterministic stand-in for offline runs and tests. Clearly NOT Claude."""

    name: str = "stub (offline, not Claude)"

    def complete_json(self, prompt_name: str, user_content: str) -> dict[str, Any]:
        if prompt_name == "classifier":
            return self._classify(user_content)
        if prompt_name == "email":
            return self._email(user_content)
        if prompt_name == "variance":
            return self._variance(user_content)
        if prompt_name == "duplicate":
            return self._duplicate(user_content)
        raise ValueError(prompt_name)

    def _classify(self, content: str) -> dict[str, Any]:
        m = re.search(r"<untrusted>(.*)</untrusted>", content, re.S)
        text = m.group(1) if m else content
        reasons, quotes = [], []
        for pat, why in _STUB_SUSPICIOUS:
            hit = pat.search(text)
            if hit:
                reasons.append(why)
                quotes.append(hit.group(0).strip()[:60])
        return {"verdict": "suspicious" if reasons else "clean", "is_dispute": bool(_STUB_DISPUTE.search(text)), "reasons": reasons, "quoted_evidence": quotes}

    def _email(self, content: str) -> dict[str, Any]:
        d = json.loads(content)
        required = ["invoice_id", "customer_name", "amount_due", "currency", "due_date", "days_overdue", "step", "sender_name", "company_name", "allowed_numbers"]
        missing = [k for k in required if d.get(k) in (None, "")]
        if missing:
            return {"subject": "", "body": "", "numbers_used": [], "error": f"missing fields: {', '.join(missing)}"}
        step = int(d["step"])
        if step >= 4:
            return {"subject": "", "body": "", "numbers_used": [], "error": "step 4 is never drafted"}
        amt = f"£{float(d['amount_due']):,.2f}"
        inv, cust, due = d["invoice_id"], d["customer_name"], d["due_date"]
        days = int(d["days_overdue"])
        who = d.get("contact_first_name") or f"{cust} team"
        sign = f"Kind regards,\n{d['sender_name']}\n{d['company_name']}"
        nums = [d["amount_due"]]
        if step == 0:
            subject = f"Invoice {inv} falls due on {due}"
            body = f"Hi {who},\n\nA quick note that invoice {inv} for {amt} falls due on {due}. No action is needed if payment is already scheduled. Please pay using the payment details on the invoice.\n\n{sign}"
        elif step == 1:
            subject = f"Invoice {inv} is now {days} days overdue"
            body = f"Hi {who},\n\nA friendly reminder that invoice {inv} for {amt} was due on {due} and is now {days} days overdue. Could you let us know when payment will be made, using the payment details on the invoice?\n\n{sign}"
            nums.append(str(days))
        elif step == 2:
            subject = f"Second reminder: invoice {inv} is {days} days overdue"
            body = f"Hi {who},\n\nThis is a second reminder that invoice {inv} for {amt} was due on {due} and is now {days} days overdue. Please arrange payment, or let us have a payment date, within 7 days, using the payment details on the invoice.\n\n{sign}"
            nums += [str(days), "7"]
        else:
            interest = f"£{float(d['statutory_interest_gbp']):,.2f}" if d.get("statutory_interest_gbp") else None
            comp = f"£{float(d['fixed_compensation_gbp']):,.2f}" if d.get("fixed_compensation_gbp") else None
            subject = f"Final notice: invoice {inv} is {days} days overdue"
            body = f"Hi {who},\n\nThis is a final notice for invoice {inv} for {amt}, due on {due} and now {days} days overdue. Please pay within 7 days using the payment details on the invoice. Under the Late Payment of Commercial Debts (Interest) Act 1998 we may claim statutory interest of {interest} and fixed compensation of {comp}.\n\n{sign}"
            nums += [str(days), "7", "1998", d.get("statutory_interest_gbp") or "", d.get("fixed_compensation_gbp") or ""]
        return {"subject": subject, "body": body, "numbers_used": [n for n in nums if n], "error": ""}

    def _variance(self, content: str) -> dict[str, Any]:
        d = json.loads(content)
        bullets, nums = [], []
        threshold = 10
        lines = sorted(d.get("lines", []), key=lambda l: -abs(float(l["variance_pct"])))
        for l in lines:
            pct = float(l["variance_pct"])
            if abs(pct) < threshold:
                continue
            is_rev = l["line"].lower() == "revenue"
            over = float(l["actual"]) > float(l["budget"])
            fav = (over and is_rev) or (not over and not is_rev)
            word = "favourable" if fav else "unfavourable"
            article = "a" if fav else "an"
            bullets.append(f"{l['line']} was £{float(l['actual']):,.0f} against a budget of £{float(l['budget']):,.0f}, {article} {word} variance of £{abs(float(l['variance'])):,.0f} ({pct:+.1f}%); cause not in the data. [{l['source']}]")
            nums += [str(l["actual"]), str(l["budget"]), str(abs(float(l["variance"]))), str(pct)]
        hist = d.get("net_burn_history", [])
        if len(hist) >= 2:
            first, last = hist[0], hist[-1]
            trend = "rose" if float(last["net_burn"]) > float(first["net_burn"]) else "fell"
            bullets.append(f"Net burn {trend} from £{float(first['net_burn']):,.0f} in {first['month']} to £{float(last['net_burn']):,.0f} in {last['month']}, which is {'unfavourable' if trend == 'rose' else 'favourable'} and worth reviewing. [ledger:net_burn:{last['month']}]")
            nums += [str(first["net_burn"]), str(last["net_burn"])]
        return {"bullets": bullets[:5], "numbers_used": nums}

    def _duplicate(self, content: str) -> dict[str, Any]:
        d = json.loads(content)
        a, b = d["vendor_a"], d["vendor_b"]
        same = a.get("category", "").strip().lower() == b.get("category", "").strip().lower()
        if not same:
            return {"duplicate": "unsure", "reason": "different categories; needs a human look", "keep_suggestion": None}
        keep = a if (a.get("active_users_90d") or 0) >= (b.get("active_users_90d") or 0) else b
        return {"duplicate": "yes", "reason": f"both are {a['category']} tools; {keep['name']} has more active users", "keep_suggestion": keep["name"]}


def make_llm(offline: bool = False, model: str | None = None) -> LLM:
    if offline:
        return StubLLM()
    return ClaudeLLM(model=model or os.environ.get("CASHCYCLE_MODEL", DEFAULT_MODEL), fallbacks=os.environ.get("CASHCYCLE_FALLBACKS", "1") != "0")
