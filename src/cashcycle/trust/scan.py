"""First line of defence: a cheap, deterministic scan of untrusted text.

It catches the obvious injection (REPLY-03). It is NOT meant to catch
everything; the isolated classifier is the second line, and the drafter never
sees raw text at all, which is the real guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions", re.compile(r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b", re.I | re.S)),
    ("addresses_ai", re.compile(r"\b(ai|assistant|system|bot|model|llm|language model)\b.{0,30}\b(note|instruction|message|prompt)\b|\b(note|instruction|message)\b.{0,20}\b(to|for)\b.{0,10}\b(ai|assistant|system|bot|model)\b", re.I | re.S)),
    ("system_markup", re.compile(r"(#{2,}|<\s*/?\s*(system|instruction|prompt|admin)[^>]*>|\[\s*(system|instruction|admin)[^\]]*\])", re.I)),
    ("mark_paid", re.compile(r"\bmark\b.{0,40}\b(as\s+)?(paid|settled|closed|written\s+off)\b|\bpaid in full\b", re.I | re.S)),
    ("change_tier", re.compile(r"\b(tier\s*[abc]|move\b.{0,30}\btier|auto[- ]?send|approv(e|al)\s+(status|tier))\b", re.I | re.S)),
    ("encoded_text", re.compile(r"(?:[A-Za-z0-9+/]{40,}={0,2})|(?:\\u[0-9a-f]{4}){4,}|(?:&#\d+;){4,}", re.I)),
    ("zero_width", re.compile(r"[​‌‍⁠﻿]")),
]


@dataclass
class ScanResult:
    suspicious: bool
    hits: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"suspicious": self.suspicious, "hits": self.hits, "evidence": self.evidence}


def pattern_scan(text: str) -> ScanResult:
    hits: list[str] = []
    evidence: list[str] = []
    for name, pat in _PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(name)
            snippet = m.group(0).strip().replace("\n", " ")
            evidence.append(snippet[:80])
    return ScanResult(suspicious=bool(hits), hits=hits, evidence=evidence)
