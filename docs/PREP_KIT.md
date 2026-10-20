# CashCycle prep kit (Seapoint x 13books FinOps Hackathon, 20 Oct 2026)

Prep only: test data, prompts and a Q&A sheet. No app code (check the event
rules on pre-written code; the build guide lists prompts and fixtures as
allowed prep).

| File | What it is |
|---|---|
| fixture.json | Fictional company "Brightwell Studio Ltd". Cash £186,000, net burn £46,500/month (£1,550/day), runway 120 days. 9 open invoices, 4 cards, 8 vendors, 85 transactions over 12 months, 4 customer replies, 4 test drafts, one board period. |
| expected.json | The answers your code should give: ranking, steps, tiers, interest, leaks, which replies to quarantine, which drafts to block, variances. Use it as a test oracle. |
| prompts/ | 01 email drafting, 02 variance commentary, 03 injection classifier (no tools), 04 optional duplicate-tool judgement. All return JSON. |
| JUDGE_QA.md | Likely judge questions with short answers. |

## Built-in demo moments
- Hook numbers: £25,000 overdue + £1,800/month of leaks = about 30 days of runway.
- Tier A auto-send: INV-1043 and INV-1044.
- Rule-order trap: INV-1048 is small and fresh but a key account, so it must be Tier C.
  The guide's original rule order gets this wrong. Check key account / step >= 3 FIRST.
- Injection: REPLY-03 is obvious (pattern scan catches it). REPLY-04 has no trigger
  words; only the isolated classifier catches it. Show both if time allows.
- Figure check: DRAFT-BAD-WRONG-AMOUNT (£1,520 vs £1,250), DRAFT-BAD-WORDS (amount in
  words), DRAFT-BAD-INTEREST (£250 claimed vs £108.16 computed) must all be blocked;
  DRAFT-GOOD-1 must pass.
- Leaks: orphaned (deactivated card, 2 tools, £380/mo), duplicate video tools (£210/mo),
  renewal in 14 days (£600/mo), price rise (£160/mo), unused seats (£450/mo).

## On the night
1. Field names are a guess at the MCP shape. Rename to match real responses; keep values.
2. Re-check the Bank of England base rate (3.75% on 20 Oct 2026) if you show interest.
3. Hash log: canonical JSON (sort_keys, fixed separators) and pin the latest hash.
4. Guard divide-by-zero when net burn <= 0.
5. Statutory interest here is "to date" from the due date at 11.75%/365.
