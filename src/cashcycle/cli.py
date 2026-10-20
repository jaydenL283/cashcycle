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
    inv_src = rep.sources.get("invoices", "fixture")
    print(f"COLLECTIONS  (ranked by runway impact; tier decided in code; invoices: {inv_src}; runway days on {rep.sources.get('burn', 'fixture')} net burn)")
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
        print(f"  [{l.source:6}] {l.type:24} {', '.join(l.vendor_names)[:34]:34} {gbp(l.monthly_saving):>10}/mo  {l.evidence}{j}")
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


def cmd_cashflow(args) -> int:
    """Cash-flow view from the live export: methods in and out, recurring days, 90-day forecast. Writes the dashboard data files."""
    from cashcycle.cashflow import build_cashflow

    live = Path(args.live or "data/live/r3con-tech")
    d = build_cashflow(live, date.fromisoformat(args.as_of) if args.as_of else date(2026, 10, 20))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cashflow.json").write_text(json.dumps(d, indent=1), encoding="utf-8")
    rows = [{"month": m, "direction": dr, "method": k, "total": v["total"], "transactions": v["rows"], "partial_month": m == d["as_of"][:7]} for m, dd in d["by_month"].items() for dr, methods in dd.items() for k, v in methods.items()]
    (out / "flows_by_month.json").write_text(json.dumps(rows), encoding="utf-8")
    (out / "recurring.json").write_text(json.dumps([{**r, "direction": "in" if r["direction"] == "credit" else "out", "months_seen": len(r["months_seen"])} for r in d["recurring_in"] + d["recurring_out"]]), encoding="utf-8")
    (out / "forecast_days.json").write_text(json.dumps([{**x, "items": "; ".join(x["items"])} for x in d["forecast_days"]]), encoding="utf-8")
    (out / "forecast_weekly.json").write_text(json.dumps(d["forecast_weekly"]), encoding="utf-8")
    (out / "kpis.json").write_text(json.dumps([
        {"metric": "opening_cash", "value": d["opening_cash"], "label": "Cash today (eligible, GBP)"},
        {"metric": "closing_90d", "value": d["closing_balance_90d"], "label": "Forecast cash in 90 days"},
        {"metric": "lowest_balance", "value": d["lowest_point"]["balance"], "label": "Lowest forecast balance"},
        {"metric": "lowest_date", "value": d["lowest_point"]["date"], "label": "Date of lowest balance"},
        {"metric": "card_daily_run_rate", "value": d["card_daily_run_rate"], "label": "Card spend per day (3-month avg)"},
        {"metric": "as_of", "value": d["as_of"], "label": "Snapshot date"}]), encoding="utf-8")
    print(f"{d['organisation']}  as of {d['as_of']}  ({d['source']})")
    print(f"  cash today {gbp(d['opening_cash'])}  ->  forecast in 90 days {gbp(d['closing_balance_90d'])}  (lowest {gbp(d['lowest_point']['balance'])} on {d['lowest_point']['date']})")
    print("  money out by method (full months):")
    for k, v in d["out_share_full_months"].items():
        print(f"    {k:14} {gbp(v['total']):>14}  {v['share_pct']:>5}%")
    print("  recurring money in:")
    for r in d["recurring_in"]:
        print(f"    day {r['usual_day']:>2}  {r['name'][:30]:30} {r['method']:14} {gbp(r['typical_amount']):>12}")
    print("  recurring money out (top 10):")
    for r in d["recurring_out"][:10]:
        print(f"    day {r['usual_day']:>2}  {r['name'][:30]:30} {r['method']:14} {gbp(r['typical_amount']):>12}")
    from cashcycle.insights import build_insights, write_dashboard_files

    ins = build_insights(live, date.fromisoformat(args.as_of) if args.as_of else date(2026, 10, 20))
    write_dashboard_files(ins, out)
    (out / "insights.json").write_text(json.dumps(ins, indent=1), encoding="utf-8")
    c, p, k = ins["creditors"]["summary"], ins["payroll"]["summary"], ins["costs"]["summary"]
    print(f"  creditors: {gbp(c['open_gbp_total'])} open across {c['open_bills']} GBP bills; paid bills take {c['avg_days_to_pay']} days on average, {c['avg_days_vs_due']:+.1f} days vs due date")
    print(f"  payroll: {gbp(p['last_3_months_avg'])}/month run-rate, {p['growth_over_period_pct']:+.1f}% over {p['period']} ({p['annualised_growth_pct']:+.1f}%/year); projection: " + ", ".join(f"{x['year']} {gbp(x['projected_annual_payroll'])}" for x in ins["payroll"]["projection"]))
    print(f"  costs: fixed {gbp(k['avg_fixed_monthly'])}/month vs variable {gbp(k['avg_variable_monthly'])}/month ({k['avg_fixed_share_pct']}% fixed), {k['months']}")
    print(f"  dashboard data written to {out}/")
    return 0


def cmd_ref(args) -> int:
    from cashcycle import invoice_ref as ir

    if args.ref_cmd == "make":
        r = ir.next_ref(args.existing or [], args.prefix, args.customer, date.fromisoformat(args.date) if args.date else date.today())
        print(f"{r.dashed()}   compact: {r.compact()}   period {r.period} seq {r.seq}" + (f" customer {r.customer_code}" if r.customer_code else ""))
        return 0
    if args.ref_cmd == "parse":
        try:
            r = ir.parse(args.text, prefix=args.prefix)
        except ir.InvalidReference as e:
            print(f"not a smart reference: {e}")
            return 1
        print(json.dumps({"prefix": r.prefix, "customer_code": r.customer_code, "year": r.year, "month": r.month, "seq": r.seq, "dashed": r.dashed(), "compact": r.compact()}, indent=2))
        return 0
    if args.ref_cmd == "match":
        hits = ir.find_references(args.text, prefix=args.prefix)
        if not hits:
            print("no references found")
            return 1
        for h in hits:
            print(f"found {h.invoice_ref.dashed()}  (as written: {h.reference!r})")
        return 0
    return 1


def cmd_warnings(args) -> int:
    from cashcycle.early_warning import build_warnings

    ws = build_warnings(Path(args.live or "data/live/r3con-tech"), date.fromisoformat(args.as_of) if args.as_of else date(2026, 10, 20))
    if args.json:
        print(json.dumps(ws, indent=2))
        return 0
    for direction, label in (("in", "MONEY IN"), ("out", "MONEY OUT")):
        print(label)
        for w in [w for w in ws if w["direction"] == direction]:
            print(f"  [{w['severity'].upper():6}] {w['title']}")
            print(f"           {w['detail']}")
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

    s = sub.add_parser("cashflow", help="how money comes in and goes out, recurring days, 90-day forecast; writes dashboard data")
    s.add_argument("--out-dir", default="out/dash")
    s.set_defaults(fn=cmd_cashflow)

    s = sub.add_parser("ref", help="smart invoice references: PREFIX-CUST-YYMM-SSS")
    rs = s.add_subparsers(dest="ref_cmd", required=True)
    r = rs.add_parser("make", help="next reference for a month"); r.add_argument("--prefix", default="CS"); r.add_argument("--customer"); r.add_argument("--date"); r.add_argument("--existing", nargs="*", help="references already issued")
    r = rs.add_parser("parse", help="decode a reference"); r.add_argument("text"); r.add_argument("--prefix")
    r = rs.add_parser("match", help="find references inside payment text"); r.add_argument("text"); r.add_argument("--prefix", default="CS")
    s.set_defaults(fn=cmd_ref)

    s = sub.add_parser("warnings", help="early warnings from customer and creditor payment patterns")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_warnings)

    s = sub.add_parser("verify-log", help="verify the audit chain against the pinned head")
    s.set_defaults(fn=cmd_verify_log)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
