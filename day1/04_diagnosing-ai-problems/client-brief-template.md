# Client Brief — Meridian Support Pilot

*One page. Fill every line. This is what Priya takes to her leadership.*

---

**Client & pilot**
Meridian — an AI agent that triages and resolves customer support tickets (a coordinator routing to billing / technical / account specialists). Live 3 weeks; closing tickets that aren't actually resolved.

**What's actually breaking it**
*(Name it plainly. Not the model — the system around it.)*

It was never the model. The coordinator had been given a written instruction to hand every ticket to **exactly one** specialist, and a second instruction to mark the ticket **resolved** as soon as that specialist reported back. Customers who raised two problems in one email got one of them fixed and both of them closed.

Ticket T-4471 is the pattern exactly. The customer reported SSO down for 40 staff *and* a $1,200 overcharge. The agent diagnosed the SSO fault correctly and precisely — expired Okta signing certificate, timestamped to the minute. Then it told the customer their refund claim was valid, pointed them at a different email address, and closed the ticket as resolved. No refund was ever issued. That is the ticket that landed on Priya's desk.

The agent was not confused. It was following its instructions correctly, and the instructions were wrong. Two supporting faults made it worse: the agent was the only judge of whether it had finished — nothing checked its claim before the ticket closed — and its tool list carried 17 tools, of which 10 were broken, duplicated, or dead. One of them reported the customer's plan in a way that led the agent to a confidently wrong root cause before a specialist corrected it. That is the "confidently wrong answers" complaint, with a specific cause.

**The fix**
*(What we changed, and where. Which prompt, which tool, which line.)*

Three changes, no model change, no application code touched:

1. **`system-prompt-coordinator.txt`, steps 5 and 6 (lines 12 and 23)** — replaced "each ticket is owned by exactly ONE specialist" with: list every distinct issue the customer raised, then assign each one an owner. Urgency now decides the order issues are worked, not which ones get worked at all. **This single change did all of the measured improvement.**
2. **`system-prompt-coordinator.txt`, step 6 (line 13)** — removed the instruction to always close as "resolved" and the note that escalations count against the team's SLA. The status now has to be earned: every issue needs an actual completed action behind it, or the ticket is marked escalated and goes to a human. Escalation is stated as a correct outcome, not a black mark.
3. **`coordinator-tools.json`** — cut the tool list from 17 to 7, removing the broken, duplicated, and dead tools including the one that produced the wrong plan diagnosis, and rewrote the remaining descriptions so the agent can tell them apart.
4. **The three `system-prompt-subagent-*.txt` files** — specialists now have to report what they did *not* do, never describe a fix in language that implies they performed it, and say when a request is beyond what their tools can reach. The coordinator's close check is only as good as what the specialists hand it; this stops a confident specialist writeup from producing a false close through the back door.
5. **`system-prompt-coordinator.txt`, examples block** — the five worked examples all showed single-issue tickets going to one specialist, so they demonstrated the old behaviour while the new instructions said the opposite. Replaced with three that mark the boundary in both directions: one issue to one specialist, two issues to two, and an issue no specialist can action going to a human.

Changes 1–3 fixed the failure. Changes 4 and 5 are there so it stays fixed — see the note on eval coverage below.

**Proof**
*(Before → after. Quality score moved from ____ to ____. Cost held at / dropped to $____ per run.)*

Measured the same way throughout: each ticket run **5 times**, scored on whether *every* issue the customer raised was actually actioned. We report the rate, not a single run, because a single green run proves nothing.

| | Before | After |
|---|---|---|
| T-4471 (the ticket that escalated) | **0 / 5** | **5 / 5** |
| All three tickets, incl. 2 the fix never saw | **0 / 9** | **15 / 15** |
| Cost per ticket | $0.1319 | $0.1739 |

Two of the three tickets were **held back** during the fix — different customers, different problems, one requiring the agent to admit it couldn't help and fetch a human. The fix resolved those too, first time. That is what tells us we fixed the system rather than the one complaint.

Cost rose 32% per ticket, and it should have: the agent now does roughly twice the work, because it handles both halves of a two-part ticket instead of one. Four cents per ticket to stop closing tickets that aren't fixed.

**What it would take**
*(Rough scope, and the constraint to hit — e.g. stay within current per-ticket cost.)*

The work above was about a day. Four things we'd want to do properly before scaling past pilot — the first two are controls the pilot doesn't have, the last two are the limits of what we can currently prove and what it currently costs:

- **Make the close a check, not a claim.** Today the agent decides for itself that it's finished. That should be enforced in code: the system verifies every raised issue has a completed action before a ticket is allowed to close. Right now it's an instruction the agent follows, and instructions can drift.
- **Put an approval step in front of the actions that cost money.** A specialist can currently issue a refund, change permissions, or reset a user's 2FA entirely on its own judgement, with no check outside the model. That's fine at pilot volume with one ticket in view; it isn't a control you want at scale.
- **Widen the test set — it currently has one ticket shape in it.** All three tickets we score against raise exactly two issues. So the scoreboard can prove the agent stopped dropping the second issue, but it cannot see the opposite failure: spawning specialists a ticket didn't need. We hardened against that in the prompt and checked the routing by hand on every run, but hand-checking is not a test. Single-issue, three-issue, and nothing-found tickets should all be in the graded set before this scales.
- **Turn on prompt caching.** Every turn re-sends the same ~3,300 tokens of instructions and tool definitions at full price, and a ticket takes around twelve model calls. The harness sets no cache markers at all today. This is the largest cost lever available and the only one of these four that needs an application code change rather than a prompt edit.

Constraint to hold: per-ticket cost stays under the current run rate.

**The objection we'll get**
*("Why not just use a better model?" — answer it with the numbers above.)*

We tested it, before changing anything. The larger, more expensive model scored **0 out of 5 — at 2.1× the cost per ticket** ($0.2610 vs $0.1218). Not "somewhat better." Identical failure, twice the price.

That result is the whole diagnosis in one number. The agent wasn't failing because it couldn't reason well enough; it was following a clear written instruction, correctly. A stronger model follows a bad instruction *more* reliably, not less — so paying more bought a more dependable version of the same mistake.

The fixed system runs on the **original model at $0.1739 per ticket** and resolves 15 out of 15. It is **33% cheaper than the upgrade that resolved nothing**.
