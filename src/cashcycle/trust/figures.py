"""Figure check: every number in a draft must be on the allowed list.

Claude never calculates. Code hands it `allowed_numbers`; this check proves
the draft used nothing else. Numbers written in words are blocked outright,
because they cannot be checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"(?<![A-Za-z0-9])(\d[\d,]*(?:\.\d+)?)(?![\d])")
_INVOICE_REF = re.compile(r"\b[A-Z]{2,5}-\d{2,8}\b")
_LINK = re.compile(r"(https?://|www\.)\S+", re.I)
_BANK_DETAILS = re.compile(r"\b(sort\s*code|account\s*number|iban|swift|bic)\b|\b\d{2}-\d{2}-\d{2}\b|\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.I)

_UNITS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_SCALE = "hundred|thousand|million|billion"
_NUMBER_WORDS = re.compile(rf"\b({_UNITS}|{_TENS}|{_SCALE})\b", re.I)


def _norm(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


@dataclass
class FigureCheck:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    numbers_found: list[str] = field(default_factory=list)
    numbers_rejected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "reasons": self.reasons, "warnings": self.warnings, "numbers_found": self.numbers_found, "numbers_rejected": self.numbers_rejected}


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right_candidates = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left:right]


def check_figures(
    text: str,
    allowed_numbers: list[str],
    *,
    computed_interest: str | None = None,
    max_words: int | None = None,
) -> FigureCheck:
    allowed: set[Decimal] = {d for d in (_norm(a) for a in allowed_numbers) if d is not None}
    reasons: list[str] = []
    warnings: list[str] = []
    found: list[str] = []
    rejected: list[str] = []

    # Strip invoice references (INV-1043) so the id's digits are not scanned twice.
    scrubbed = _INVOICE_REF.sub(" ", text)

    for m in _NUMBER.finditer(scrubbed):
        raw = m.group(1)
        found.append(raw)
        value = _norm(raw)
        if value is None or value not in allowed:
            rejected.append(raw)
            sentence = _sentence_around(scrubbed, m.start(), m.end()).lower()
            if "interest" in sentence:
                expected = f" (computed {computed_interest})" if computed_interest else ""
                reasons.append(f"interest figure {raw} does not match computed value{expected}")
            else:
                shown = raw if value is None or format(value.normalize(), "f") == raw else f"{raw} ({format(value.normalize(), 'f')})"
                reasons.append(f"{shown} not in allowed numbers")

    wm = _NUMBER_WORDS.search(text)
    if wm:
        reasons.append(f"number written in words: '{wm.group(0)}'")
    if _LINK.search(text):
        reasons.append("contains a link")
    if _BANK_DETAILS.search(text):
        reasons.append("contains bank details")
    if "!" in text:
        warnings.append("contains an exclamation mark")
    if max_words is not None and len(text.split()) > max_words:
        warnings.append(f"over {max_words} words")

    return FigureCheck(passed=not reasons, reasons=reasons, warnings=warnings, numbers_found=found, numbers_rejected=rejected)
