"""Smart invoice references: prefix, customer code, year, month and sequence in one string.

    CS-SRG-2610-001   dashed, for documents and emails
    CSSRG2610001      compact, for bank references (banks strip punctuation)

Fixed widths remove ambiguity: YY (2) MM (2) SSS (3). "26101" could be
2026-10 #1 or 2026-01 #01; "2610001" cannot. A payment's free-text reference
is matched by stripping spaces, dashes and case, so "cs srg 2610 001 thanks"
still finds CS-SRG-2610-001.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

PREFIX_RE = r"[A-Z]{2,4}"
CUSTOMER_RE = r"[A-Z0-9]{2,5}"
_PARSE = re.compile(rf"^(?P<prefix>{PREFIX_RE})(?P<customer>{CUSTOMER_RE})?(?P<yy>\d{{2}})(?P<mm>\d{{2}})(?P<seq>\d{{3,4}})$")
_SEP = re.compile(r"[\s\-_/.]+")
CENTURY = 2000


class InvalidReference(ValueError):
    pass


@dataclass(frozen=True)
class InvoiceRef:
    prefix: str
    customer_code: str | None
    year: int
    month: int
    seq: int

    def __post_init__(self) -> None:
        if not re.fullmatch(PREFIX_RE, self.prefix):
            raise InvalidReference(f"prefix must be 2-4 letters, got {self.prefix!r}")
        if self.customer_code is not None and not re.fullmatch(CUSTOMER_RE, self.customer_code):
            raise InvalidReference(f"customer code must be 2-5 letters or digits, got {self.customer_code!r}")
        if not CENTURY <= self.year < CENTURY + 100:
            raise InvalidReference(f"year {self.year} outside {CENTURY}-{CENTURY + 99}")
        if not 1 <= self.month <= 12:
            raise InvalidReference(f"month {self.month} outside 1-12")
        if not 1 <= self.seq <= 9999:
            raise InvalidReference(f"sequence {self.seq} outside 1-9999")

    @property
    def period(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def digits(self) -> str:
        width = 3 if self.seq < 1000 else 4
        return f"{self.year % 100:02d}{self.month:02d}{self.seq:0{width}d}"

    def dashed(self) -> str:
        parts = [self.prefix] + ([self.customer_code] if self.customer_code else []) + [self.digits[:4], self.digits[4:]]
        return "-".join(parts)

    def compact(self) -> str:
        return f"{self.prefix}{self.customer_code or ''}{self.digits}"

    def __str__(self) -> str:
        return self.dashed()


def normalise(text: str) -> str:
    return _SEP.sub("", text.strip()).upper()


def parse(text: str, *, prefix: str | None = None) -> InvoiceRef:
    """Parse a reference in any spacing or case. With `prefix`, the customer code is read unambiguously."""
    s = normalise(text)
    if prefix:
        p = prefix.upper()
        if not s.startswith(p):
            raise InvalidReference(f"{text!r} does not start with prefix {p}")
        rest = s[len(p):]
        m = re.fullmatch(rf"(?P<customer>{CUSTOMER_RE})?(?P<yy>\d{{2}})(?P<mm>\d{{2}})(?P<seq>\d{{3,4}})", rest)
        if not m:
            raise InvalidReference(f"{text!r} is not a smart reference (expected {p}[CUST]YYMMSSS)")
        return InvoiceRef(p, m["customer"], CENTURY + int(m["yy"]), int(m["mm"]), int(m["seq"]))
    m = _PARSE.match(s)
    if not m:
        raise InvalidReference(f"{text!r} is not a smart reference (expected PREFIX[CUST]YYMMSSS with fixed widths)")
    # Without a known prefix, letters split greedily: 2-4 for the prefix, the rest is the customer code.
    letters = m["prefix"] + (m["customer"] or "")
    if len(letters) <= 4:
        pre, cust = letters, None
    else:
        pre, cust = letters[:2], letters[2:]
    return InvoiceRef(pre, cust, CENTURY + int(m["yy"]), int(m["mm"]), int(m["seq"]))


def is_smart(text: str, *, prefix: str | None = None) -> bool:
    try:
        parse(text, prefix=prefix)
        return True
    except InvalidReference:
        return False


def next_ref(existing: Iterable[str], prefix: str, customer_code: str | None, on: date) -> InvoiceRef:
    """The next sequence number for this prefix and month. Sequences run per month, across customers."""
    p = prefix.upper()
    used = 0
    for e in existing:
        try:
            r = parse(e, prefix=p)
        except InvalidReference:
            continue
        if r.year == on.year and r.month == on.month:
            used = max(used, r.seq)
    return InvoiceRef(p, customer_code.upper() if customer_code else None, on.year, on.month, used + 1)


@dataclass(frozen=True)
class Match:
    reference: str
    invoice_ref: InvoiceRef
    position: int


def find_references(text: str, *, prefix: str) -> list[Match]:
    """Find every smart reference for `prefix` inside free text, e.g. a bank payment reference."""
    p = prefix.upper()
    # letters and digits may be separated by spaces/dashes in the statement text
    pattern = re.compile(rf"\b{p}[\s\-_/.]*(?:{CUSTOMER_RE})?[\s\-_/.]*\d{{2}}[\s\-_/.]*\d{{2}}[\s\-_/.]*\d{{3,4}}\b", re.I)
    out = []
    for m in pattern.finditer(text):
        try:
            out.append(Match(m.group(0), parse(m.group(0), prefix=p), m.start()))
        except InvalidReference:
            continue
    return out


def match_payment(payment_text: str, open_invoices: dict[str, str], *, prefix: str) -> list[tuple[str, InvoiceRef]]:
    """Which open invoices (id -> reference) does a payment's reference text mention?"""
    wanted = {}
    for inv_id, ref in open_invoices.items():
        try:
            wanted[parse(ref, prefix=prefix).compact()] = (inv_id, parse(ref, prefix=prefix))
        except InvalidReference:
            continue
    hits = []
    for m in find_references(payment_text, prefix=prefix):
        key = m.invoice_ref.compact()
        if key in wanted and wanted[key] not in hits:
            hits.append(wanted[key])
    return hits
