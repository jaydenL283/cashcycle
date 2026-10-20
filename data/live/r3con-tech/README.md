# Live Seapoint export (staging, organisation "Hackathon - R3CON Tech")

Saved verbatim from the Seapoint MCP on 2026-10-20 so the app can run against the
real response shape without a live connection. Each file is one tool call's
`data` array. Regenerate with the `cashcycle` agent (see `.claude/agents/cashcycle.md`).

| File | Tool call |
|---|---|
| accounts.json | `query_accounts` for the organisation (all pages) |
| cards.json | `query_cards` for the organisation |
| burn_by_month.json | `query_transactions` aggregate, net-mode shape, Jul-Sep 2026, grouped by month/direction/type |
| card_spend_by_vendor_month.json | `query_transactions` aggregate, card payments Jun-Oct 2026 grouped by participant and month |
| categories_financial_items.json | `query_categories` for the "Financial Items" group (Capital Investments id feeds the net-burn call) |
| invoices.json | `query_invoices` for the organisation (supplier bills; all 39 rows, trimmed to the fields the live checks use) |
| card_activity.json | `query_transactions` aggregate per non-active card, grouped by month and status |

What the MCP does not expose (customer invoices, vendor seat usage, customer replies,
budget lines) still comes from `fixtures/fixture.json` and is labelled "fixture" in output.
