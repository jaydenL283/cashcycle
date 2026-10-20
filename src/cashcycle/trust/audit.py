"""Hash-chained audit log.

Each record carries the previous record's hash; the head hash is pinned
outside the log. Edit any record and rebuild the chain and it no longer
matches the pinned head. Records hold hashes of drafts, not the drafts, so the
log carries no extra personal data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    """Canonical JSON: sorted keys, fixed separators, no NaN, UTF-8 safe."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(obj: Any) -> str:
    return sha256(canonical(obj))


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: str
    agent: str
    action: str
    subject: str
    detail: dict[str, Any]
    prev_hash: str
    hash: str

    @staticmethod
    def compute_hash(seq: int, ts: str, agent: str, action: str, subject: str, detail: dict[str, Any], prev_hash: str) -> str:
        body = {"seq": seq, "ts": ts, "agent": agent, "action": action, "subject": subject, "detail": detail, "prev_hash": prev_hash}
        return content_hash(body)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "agent": self.agent, "action": self.action, "subject": self.subject, "detail": self.detail, "prev_hash": self.prev_hash, "hash": self.hash}


class AuditLog:
    def __init__(self, path: Path | None = None, head_path: Path | None = None):
        self.path = path
        self.head_path = head_path
        self.records: list[AuditRecord] = []
        if path and path.exists():
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        d = json.loads(line)
                        self.records.append(AuditRecord(**d))

    @property
    def head(self) -> str:
        return self.records[-1].hash if self.records else GENESIS

    def append(self, agent: str, action: str, subject: str, detail: dict[str, Any] | None = None, ts: str | None = None) -> AuditRecord:
        detail = detail or {}
        seq = len(self.records) + 1
        ts = ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
        prev = self.head
        h = AuditRecord.compute_hash(seq, ts, agent, action, subject, detail, prev)
        rec = AuditRecord(seq, ts, agent, action, subject, detail, prev, h)
        self.records.append(rec)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical(rec.to_dict()) + "\n")
        self.pin()
        return rec

    def pin(self) -> str:
        """Write the head hash outside the log. On a real deployment this is signed or shown on screen."""
        if self.head_path:
            self.head_path.parent.mkdir(parents=True, exist_ok=True)
            self.head_path.write_text(self.head + "\n", encoding="utf-8")
        return self.head

    def verify(self, pinned_head: str | None = None) -> tuple[bool, list[str]]:
        problems: list[str] = []
        prev = GENESIS
        for i, rec in enumerate(self.records, start=1):
            if rec.seq != i:
                problems.append(f"record {i}: sequence gap (seq={rec.seq})")
            if rec.prev_hash != prev:
                problems.append(f"record {i}: prev_hash does not match record {i - 1}")
            expected = AuditRecord.compute_hash(rec.seq, rec.ts, rec.agent, rec.action, rec.subject, rec.detail, rec.prev_hash)
            if rec.hash != expected:
                problems.append(f"record {i}: hash does not match content")
            prev = rec.hash
        if pinned_head is None and self.head_path and self.head_path.exists():
            pinned_head = self.head_path.read_text(encoding="utf-8").strip()
        if pinned_head is not None and pinned_head != self.head:
            problems.append("chain head does not match the pinned head")
        return (not problems, problems)
