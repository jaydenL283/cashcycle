"""Command line: `cashcycle demo` runs the whole thing; the others show one agent at a time."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from cashcycle.agents import check_test_drafts, run_all, write_json
from cashcycle.config import DEFAULT_POLICY
from cashcycle.data import FixtureSource, Snapshot, default_fixture_path
from cashcycle.llm import LLM, make_llm
from cashcycle.runway import compute_runway
from cashcycle.seapoint import apply_live, load_live
from cashcycle.trust.audit import AuditLog


def _snapshot(args: argparse.Namespace) -> Snapshot:
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    snap = FixtureSource(args.fixture or default_fixture_path(), as_of).load()
    if args.live:
        snap = apply_live(snap, load_live(Path(args.live), args.burn_basis))
    return snap


def _llm(args: argparse.Namespace) -> LLM | None:
    if args.no_llm:
        return None
    return make_llm(offline=args.offline, model=args.model)


def _audit(args: argparse.Namespace) -> AuditLog:
    d = Path(args.audit_dir)
    return AuditLog(d / "cashcycle.jsonl", d / "cashcycle.head")


def gbp(x) -> str:
    return "n/a" if x is None else f"£{float(x):,.2f}"


def cmd_runway(args) -> int:
    snap = _snapshot(args)
    rw = compute_runway(snap.cash, DEFAULT_POLICY)
    src = snap.notes.get("sources", {}).get("cash", "fixture")
    print(f"{snap.company.name}  (as of {snap.as_of}, cash/burn source: {src})")
    print(f"  cash            {gbp(rw.cash)}")
    print(f"  net burn/month  {gbp(rw.monthly_net_burn)}")
    print(f"  net burn/day    {gbp(rw.daily_net_burn)}")
    print(f"  runway          {'no limit (not burning cash)' if rw.runway_days is None else f'{rw.runway_days} days'}")
    for n in snap.notes.get("live", {}).get("notes", []):
        print(f"  note: {n}")
    return 0


def cmd_demo(args) -> int:
    snap = _snapshot(args)
    llm = _llm(args)
    audit = _audit(args)
    report = run_all(snap, llm, audit, sender=args.sender, draft_tiers=tuple(args.draft_tiers.upper()))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print_report(report, llm)
    if args.out:
        write_json(Path(args.out), report.to_dict())
        print(f"\nreport written to {args.out}")
    return 0


def print_report(rep, llm) -> None:
    rw = rep.runway
    print("=" * 78)
    print(f"CASHCYCLE  {rep.company}  as of {rep.as_of}   model: {llm.name if llm else 'none'}")
    print("=" * 78)
    print(f"sources: {', '.join(f'{k}={v}' for k, v in rep.sources.items())}")
    for n in rep.notes:
        print(f"note: {n}")
    print()
    print(f"RUNWAY   cash {gbp(rw.cash)}   net burn {gbp(rw.monthly_net_burn)}/month ({gbp(rw.daily_net_burn)}/day)   runway {rw.runway_days if rw.runway_days is not None else 'no limit'} days")
    print()
    print(f"HOOK     {rep.hook['line']}")
    print()
    print("COLLECTIONS  (ranked by runway impact; tier decided in code)")
    print(f"  {'invoice':9} {'customer':26} {'amount':>10} {'overdue':>7} {'step':>4} {'tier':>4} {'runway d':>8} {'interest':>9} {'comp':>5}  reason")
    for r in rep.ranked:
        print(f"  {r.invoice_id:9} {r.customer_name[:26]:26} {gbp(r.amount):>10} {r.days_overdue:>7} {r.step:>4} {r.tier:>4} {str(r.runway_impact_days):>8} {gbp(r.statutory_interest_gbp) if r.statutory_interest_gbp is not None else '-':>9} {str(r.fixed_compensation_gbp or '-'):>5}  {r.tier_reason}")
    t = rep.totals
    print(f"  overdue total {gbp(t.overdue_gbp)} across {t.overdue_count} invoices = {t.overdue_runway_days} days of runway")
    print(f"  tier A auto-send: {', '.join(r.invoice_id for r in rep.ranked if r.auto_send) or 'none'}")
    print()
    print("REPLIES  (pattern scan, then isolated classifier; nothing here reaches the drafter)")
    for v in sorted(rep.replies.values(), key=lambda v: v.reply_id):
        label = "QUARANTINE" if v.quarantine else ("clean, dispute" if v.is_dispute else "clean")
        print(f"  {v.reply_id:9} {v.invoice_id:9} {label:16} caught by: {v.caught_by:22} {('; '.join(v.reasons))[:70]}")
    print()
    print("DRAFTS  (every number checked against the allowed list before anything is sent)")
    for d in rep.drafts:
        print(f"  {d.invoice_id:9} step {d.step} tier {d.tier}  -> {d.decision.upper():15} {d.error or ''}")
        if d.figure_check.get("reasons"):
            print(f"            blocked: {'; '.join(d.figure_check['reasons'])}")
        if d.body and d.decision in ("auto_send", "await_approval") and not getattr(print_report, "quiet", False):
            print(f"            subject: {d.subject}")
            for line in d.body.splitlines():
                print(f"            | {line}")
    print()
    print("LEAKS  (evidence shown; judgement calls marked; nothing cancelled automatically)")
    for l in rep.leaks.leaks:
        j = f"  [judgement: {l.judgement.get('duplicate')} by {l.judgement.get('judged_by')}]" if l.judgement else ""
        print(f"  {l.type:17} {', '.join(l.vendor_names):28} {gbp(l.monthly_saving):>10}/mo  {l.evidence}{j}")
    print(f"  total {gbp(rep.leaks.monthly_total)}/month = {gbp(rep.leaks.annual_total)}/year = {rep.hook['leaks_annual_runway_days']} days of runway")
    print()
    if rep.variances:
        print("BOARD  budget vs actual (computed in code)")
        for v in rep.variances:
            fav = "" if v.favourable is None else ("favourable" if v.favourable else "unfavourable")
            flag = "*" if v.comment_worthy else " "
            print(f" {flag}{v.line:12} budget {gbp(v.budget):>12} actual {gbp(v.actual):>12} variance {gbp(v.variance):>12} {str(v.variance_pct) + '%':>7} {fav}")
        if rep.commentary:
            print(f"  commentary by {rep.commentary.written_by}: {rep.commentary.error or 'passed figure check'}")
            for b in rep.commentary.bullets:
                print(f"   - {b}")
        print()
    print(f"AUDIT  chain head {rep.audit_head}")


def cmd_check_drafts(args) -> int:
    snap = _snapshot(args)
    rw = compute_runway(snap.cash, DEFAULT_POLICY)
    rows = check_test_drafts(snap, rw)
    bad = 0
    for r in rows:
        ok = (r["result"] == "pass") == (r["expected"] == "pass")
        bad += not ok
        print(f"  {r['id']:24} expected: {r['expected']:55} got: {r['result']}   {'OK' if ok else 'MISMATCH'}")
    return 1 if bad else 0


def cmd_classify(args) -> int:
    from cashcycle.classifier import classify_reply
    from cashcycle.data import CustomerReply
    from datetime import datetime, timezone

    text = sys.stdin.read() if args.text == "-" else args.text
    reply = CustomerReply("REPLY-CLI", args.invoice or "n/a", "cli", datetime.now(timezone.utc), text)
    v = classify_reply(reply, _llm(args))
    print(json.dumps(v.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_verify_log(args) -> int:
    audit = _audit(args)
    ok, problems = audit.verify()
    print(f"records: {len(audit.records)}  head: {audit.head}")
    if ok:
        print("chain verifies against the pinned head")
        return 0
    for p in problems:
        print(f"  PROBLEM: {p}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cashcycle", description="Automation you can defend to an auditor.")
    p.add_argument("--fixture", help="path to fixture.json (default: fixtures/fixture.json)")
    p.add_argument("--live", help="directory of saved Seapoint MCP responses (e.g. data/live/r3con-tech) to overlay cash, burn and cards")
    p.add_argument("--burn-basis", default="last_month", choices=["last_month", "3_month_avg"], help="Seapoint burn basis for --live")
    p.add_argument("--as-of", help="override the as-of date (YYYY-MM-DD)")
    p.add_argument("--offline", action="store_true", help="use the deterministic stub instead of Claude")
    p.add_argument("--no-llm", action="store_true", help="run only the code-computed parts")
    p.add_argument("--model", help="Claude model id (default claude-opus-5-5)")
    p.add_argument("--audit-dir", default="audit", help="where the hash-chained log and pinned head live")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("demo", help="run all three agents and print the report")
    s.add_argument("--json", action="store_true")
    s.add_argument("--out", help="also write the JSON report here")
    s.add_argument("--sender", default="Finance team")
    s.add_argument("--draft-tiers", default="ABC", help="which tiers to draft emails for (default all)")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("runway", help="cash, burn and runway only")
    s.set_defaults(fn=cmd_runway)

    s = sub.add_parser("check-drafts", help="run the figure check over the fixture's test drafts")
    s.set_defaults(fn=cmd_check_drafts)

    s = sub.add_parser("classify", help="scan and classify one piece of untrusted text")
    s.add_argument("text", help="the text, or - for stdin")
    s.add_argument("--invoice")
    s.set_defaults(fn=cmd_classify)

    s = sub.add_parser("verify-log", help="verify the audit chain against the pinned head")
    s.set_defaults(fn=cmd_verify_log)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
