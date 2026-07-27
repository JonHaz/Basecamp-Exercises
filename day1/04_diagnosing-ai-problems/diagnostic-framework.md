# The Diagnostic Loop

A repeatable framework for diagnosing any AI system failure — from a single broken prompt to a fifty-agent pipeline.

*(The prompts below tell you how to apply each step to Meridian yourself; your facilitator will walk through the worked answers at the end of the session.)*

---

## Step 1 — Symptom
**What the customer reports, in their words.**

Don't reframe it yet. Don't add your interpretation. Write down exactly what they said is wrong, as they said it.

*In Meridian:* before anything else, write Priya's complaint as one plain sentence, in her words — not yours.

---

## Step 2 — Hypothesis
**What you suspect before seeing any internals.**

Force at least three hypotheses from the symptom alone. Don't jump to the artifacts. Naming hypotheses before you look commits you to a position — and teaches you where your diagnostic reflexes are strong or weak.

Common structural hypotheses:
- Routing / classification failure
- Tool description too vague to use reliably
- Missing or wrong escalation path
- Sub-agent over-claiming resolution
- Context not reaching the model that needs it
- Cache placement breaking shared prompt regions

*In Meridian:* from the email alone — before opening any file — commit to three hypotheses, from the list above or your own. Write them down; you'll grade them against evidence in Step 3.

---

## Step 3 — Evidence
**What the artifacts confirm or rule out.**

For any agentic system, the first three things to ask for:
1. **System prompts** — what is the agent trying to do?
2. **Tool descriptions** — what can the model see and when would it use each tool?
3. **Execution traces** — what actually happened, step by step?

Read each artifact against your hypotheses. Point to the specific line that confirms or rules out each one. "The prompt seems off" is not evidence — "line 12 tells the agent to retry forever with no timeout" is.

*In Meridian:* read the trace first, then the coordinator's prompt and tool list. For each Step-2 hypothesis, find the exact line that confirms or kills it — and quote it. A hypothesis you can't tie to a line isn't confirmed yet.

---

## Step 4 — Recommendation
**A fix scoped to the actual root cause.**

Not "improve the prompt." Scoped: which prompt, which line, what change, and why it would prevent the specific failure you just diagnosed.

If you can't write a recommendation that references the exact artifact and line, go back to Step 3.

*In Meridian:* for each root cause you confirmed, write the fix as: file → line → change → the failure it prevents. Then make the change and rerun the scoreboard. Step 4 isn't done until the rate moves.

---

## Quick reference

| Step | Question | Output |
|------|----------|--------|
| Symptom | What is the customer actually reporting? | Plain-English problem statement |
| Hypothesis | What could cause this? (3 minimum, before artifacts) | Ranked hypothesis list |
| Evidence | Which hypothesis does each artifact confirm or rule out? | Line-cited evidence per hypothesis |
| Recommendation | What change, in which file, prevents this failure? | Scoped fix per root cause |

---

*Diagnosing AI Problems · Partner Basecamp Day 1*
