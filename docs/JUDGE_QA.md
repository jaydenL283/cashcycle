# CashCycle: likely judge questions and answers

One line to leave with them: **"Automation you can defend to an auditor."**

**1. Why rank by runway impact, not by how old the invoice is?**
Founders think in runway. An £8,400 invoice 40 days late buys 5.4 days of runway; a £950 one buys 0.6. Chasing in impact order recovers the most runway for the same effort. Age still sets the tone of the email (escalation step).

**2. What stops Claude inventing a number?**
Claude never calculates. Code computes every figure, gives Claude a list of allowed numbers, and checks every number in the draft against that list before anything is sent. Numbers written in words are blocked too. We show a draft with a wrong amount being blocked.

**3. Why is it safe to auto-send anything?**
Only Tier A auto-sends: small, at most 7 days overdue, not disputed, not a key account, no flagged reply. Tiers are decided in code, not by the model. Everything else waits for one-click approval, and every send is logged.

**4. What if a prompt injection gets past the classifier?**
The classifier is the second line, not the first. The drafting agent never sees raw customer text, only structured fields from code. So even a missed injection cannot change an amount, a tier or a recipient. The classifier decides whether a human looks at the reply.

**5. The hash chain: can't someone just recompute all the hashes after editing?**
Yes, which is why the latest hash is pinned outside the log (shown on screen at start / signed). Edit a record and rebuild the chain, and it no longer matches the pinned head. [Only say this if you built it.]

**6. Is charging statutory interest legal?**
Yes, between businesses under the Late Payment of Commercial Debts (Interest) Act 1998: 8% above the Bank of England base rate (3.75% on 20 Oct 2026, so 11.75%), daily at 1/365, plus a fixed sum once per invoice (£40 under £1,000, £70 up to £9,999.99, £100 from £10,000). Not if the contract sets its own rate. We only mention it at the final-notice step, and a human approves those.

**7. Leakfinder false positives?**
Every finding shows its evidence (transactions, card status, seat usage). Judgement calls ("are these duplicates?") are marked as judgement and go to a human. Nothing is cancelled automatically.

**8. What if the MCP doesn't have the data?**
[Be honest on the night.] We used the live MCP for cash, burn and runway, and a fixture of the same shape for anything it doesn't expose, which is labelled clearly.

**9. Customer data and privacy?**
Read-only access to the finance data; emails go only to the invoice's contact; untrusted text is quarantined, not stored in prompts; audit log holds hashes of drafts, not extra personal data.

**10. Why three agents and not one?**
One shared data layer and one shared trust layer; three narrow jobs. Each is simple enough to test, and they all inherit the same safety guarantees.

**11. Who pays for this?**
Seed to Series A startups and their fractional finance teams: it recovers cash and cuts spend, so it pays for itself in runway days.

**12. What would you build next?**
Payment-probability ranking, real email sending behind approvals, accounting-system write-back with approval, and signed audit logs an auditor can verify independently.
