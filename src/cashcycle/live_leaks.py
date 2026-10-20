"""Leakfinder checks that run on live Seapoint data (supplier bills and card activity).

Each finding is labelled "live". Nothing is cancelled or marked; a person acts.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from cashcycle.data import money
from cashcycle.leakfinder import Leak
from cashcycle.runway import q2

VAT_TOLERANCE = Decimal("0.02")  # rounding slack on net x rate


def duplicate_bills(invoices: list[dict]) -> list[Leak]:
    """Same vendor, same reference, same total, same currency: a bill captured twice."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for inv in invoices:
        key = (
            (inv.get("vendor") or {}).get("name", "").strip().lower(),
            (inv.get("reference") or "").strip().upper(),
            money((inv.get("amounts") or {}).get("total")),
            (inv.get("amounts") or {}).get("currency"),
        )
        if key[0] and key[1]:
            groups[key].append(inv)
    out = []
    for (vendor, ref, total, ccy), rows in groups.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r.get("created_at", ""))
        unpaid = [r for r in rows if not r.get("is_paid")]
        sources = ", ".join(f"{r['id']} ({r.get('source')})" for r in rows)
        out.append(
            Leak(
                type="duplicate_bill",
                vendor_ids=[r["id"] for r in rows],
                vendor_names=[rows[0]["vendor"]["name"]],
                # Paying both would cost one extra bill; counted once, not monthly.
                monthly_saving=Decimal("0"),
                evidence=f"{ref} {ccy} {total:,.2f} captured {len(rows)} times: {sources}; {len(unpaid)} unpaid. One-off exposure {ccy} {total * (len(rows) - 1):,.2f} if both are paid",
                transactions=[r["id"] for r in rows],
                judgement=None,
                source="live",
            )
        )
    return out


def vat_mismatches(invoices: list[dict]) -> list[Leak]:
    """sales_tax should equal net x sales_tax_rate; a gap means a mis-read or a mis-keyed bill."""
    out = []
    for inv in invoices:
        a = inv.get("amounts") or {}
        if a.get("net") is None or a.get("sales_tax") is None or a.get("sales_tax_rate") is None:
            continue
        net, tax, rate = money(a["net"]), money(a["sales_tax"]), money(a["sales_tax_rate"])
        expected = q2(net * rate / Decimal(100))
        if abs(expected - tax) <= VAT_TOLERANCE:
            continue
        total_expected = q2(net + expected)
        out.append(
            Leak(
                type="vat_mismatch",
                vendor_ids=[inv["id"]],
                vendor_names=[(inv.get("vendor") or {}).get("name", "?")],
                monthly_saving=Decimal("0"),
                evidence=f"{inv.get('reference')}: net {net:,.2f} x {rate}% = {expected:,.2f} expected VAT, bill shows {tax:,.2f} (gap {expected - tax:,.2f}); total {money(a.get('total')):,.2f} vs {total_expected:,.2f} if VAT were right. Check the document before paying",
                transactions=[inv["id"]],
                source="live",
            )
        )
    return out


def non_active_card_charges(cards: list[dict], activity: list[dict]) -> list[Leak]:
    """Charges on a card whose status is cancelled, frozen or expired. The MCP gives no status date."""
    by_card = {c["id"]: c for c in cards}
    out = []
    for entry in activity:
        card = by_card.get(entry.get("card_id"))
        if not card or card.get("status") == "active":
            continue
        groups = [g for g in entry.get("groups", []) if g.get("group", {}).get("status") not in ("FAILED", "CANCELLED", "FULL_REVERSAL")]
        if not groups:
            continue
        total = sum((money(g["metrics"]["total"]) for g in groups), Decimal("0"))
        rows = sum(int(g["metrics"].get("rows", 0)) for g in groups)
        months = sorted({g["group"]["date"][:7] for g in groups})
        last = max(g["metrics"].get("last_seen", "") for g in groups)[:10]
        out.append(
            Leak(
                type="non_active_card_charges",
                vendor_ids=[card["id"]],
                vendor_names=[f"{card.get('name')} ({card.get('holder', {}).get('name')}, *{card.get('last_four')})"],
                monthly_saving=q2(total / Decimal(len(months))),
                evidence=f"card status {card.get('status')} (status date unknown: not exposed by the MCP); {rows} booked charges totalling £{total:,.2f} across {', '.join(months)}, last on {last}. Confirm whether these pre-date the status change",
                transactions=[],
                card_id=card["id"],
                source="live",
            )
        )
    return out


def find_live_leaks(invoices: list[dict], cards: list[dict], activity: list[dict]) -> list[Leak]:
    return duplicate_bills(invoices) + vat_mismatches(invoices) + non_active_card_charges(cards, activity)
