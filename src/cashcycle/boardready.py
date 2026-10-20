"""BoardReady agent: budget vs actual, computed in code, explained by the model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from cashcycle.config import DEFAULT_POLICY, Policy
from cashcycle.data import BoardPeriod
from cashcycle.llm import LLM, LLMError
from cashcycle.runway import Runway, pct1
from cashcycle.trust.figures import check_figures

REVENUE_LINES = {"revenue", "sales", "income"}


@dataclass
class Variance:
    line: str
    budget: Decimal
    actual: Decimal
    variance: Decimal
    variance_pct: Decimal | None
    favourable: bool | None
    source: str
    comment_worthy: bool

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "budget": float(self.budget),
            "actual": float(self.actual),
            "variance": float(self.variance),
            "variance_pct": float(self.variance_pct) if self.variance_pct is not None else None,
            "favourable": self.favourable,
            "source": self.source,
            "comment_worthy": self.comment_worthy,
        }


def compute_variances(board: BoardPeriod, policy: Policy = DEFAULT_POLICY) -> list[Variance]:
    out = []
    for l in board.lines:
        var = l.actual - l.budget
        pct = pct1(var / l.budget * 100) if l.budget != 0 else None
        is_rev = l.line.strip().lower() in REVENUE_LINES
        fav: bool | None
        if var == 0:
            fav = None
        else:
            fav = (var > 0) if is_rev else (var < 0)
        worthy = pct is not None and abs(pct) >= policy.variance_comment_threshold_pct
        out.append(Variance(l.line, l.budget, l.actual, var, pct, fav, l.source, worthy))
    return out


@dataclass
class Commentary:
    bullets: list[str]
    numbers_used: list[str]
    figure_check: dict
    written_by: str
    error: str | None = None

    def to_dict(self) -> dict:
        return {"bullets": self.bullets, "numbers_used": self.numbers_used, "figure_check": self.figure_check, "written_by": self.written_by, "error": self.error}


def commentary_input(board: BoardPeriod, variances: list[Variance], runway: Runway) -> dict:
    return {
        "period": board.period,
        "currency": board.currency,
        "lines": [{"line": v.line, "budget": float(v.budget), "actual": float(v.actual), "variance": float(v.variance), "variance_pct": float(v.variance_pct) if v.variance_pct is not None else None, "source": v.source} for v in variances],
        "net_burn_history": [{"month": h.month, "net_burn": float(h.net_burn)} for h in board.net_burn_history],
        "runway_days": float(runway.runway_days) if runway.runway_days is not None else None,
    }


def allowed_numbers_for_board(payload: dict) -> list[str]:
    nums: list[str] = []
    for l in payload["lines"]:
        for k in ("budget", "actual", "variance", "variance_pct"):
            if l[k] is not None:
                nums += [str(l[k]), str(abs(l[k])), f"{abs(l[k]):.0f}", f"{abs(l[k]):.1f}", f"{abs(l[k]):.2f}"]
    for h in payload["net_burn_history"]:
        nums += [str(h["net_burn"]), f"{h['net_burn']:.0f}", f"{h['net_burn']:.2f}"]
        y, m = h["month"].split("-")
        nums += [y, m, str(int(m))]
    if payload.get("runway_days") is not None:
        nums += [str(payload["runway_days"]), f"{payload['runway_days']:.0f}"]
    y, m = payload["period"].split("-")
    nums += [y, m, str(int(m))]
    return sorted(set(nums))


def write_commentary(board: BoardPeriod, variances: list[Variance], runway: Runway, llm: LLM | None) -> Commentary:
    payload = commentary_input(board, variances, runway)
    allowed = allowed_numbers_for_board(payload)
    if llm is None:
        return Commentary([], [], {}, "none", "no model configured")
    try:
        out = llm.complete_json("variance", json.dumps(payload))
    except LLMError as e:
        return Commentary([], [], {}, llm.name, str(e))
    bullets = list(out.get("bullets", []))
    # Figure check across all bullets; a bullet with an unknown number blocks the whole commentary.
    check = check_figures("\n".join(bullets), allowed)
    return Commentary(bullets if check.passed else [], list(out.get("numbers_used", [])), check.to_dict(), llm.name, None if check.passed else "blocked by figure check")
