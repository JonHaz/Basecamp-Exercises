# Prompt Rescue — customer debrief

**TechSupport Corp · support-ticket triage pipeline · 3 minutes**

Everything below is measured in `Prompt_Rescue_solo.ipynb` against your own 21-case eval
harness, on `claude-haiku-4-5`. The notebook was executed end to end twice; where the two
executions disagree, the range is reported rather than the better number.

---

## 1. What was actually wrong

Your prompt was not "bad at messy tickets". It had four specific defects, and your own harness
points at every one of them.

**a. Hedge-as-value.** `Always include all JSON fields even if empty` reads to the model as
*fill this in*, not *leave it null*. On a ticket that named no product it wrote one:
`product="CRM Platform"` (case 1), `product="Cloud-hosted platform"` (case 13),
`product="Unknown - platform not specified"` (case 7). On a ticket with no count it estimated
one: `affected_users="3 out of 6 team members"` (case 10),
`affected_users="Multiple teams (at least 2+ departments)"` (case 21). Cases 15 and 16
invented error codes outright. This is not an extraction bug — it is the prompt doing exactly
what it was told.

**b. Tone drives priority.** The rubric classifies on business impact, but nothing tells the
model to ignore *register*. Case 11 — a dark-mode feature request written in capitals, calling
itself critical and threatening to leave — came back **P1** against a gold label of **P4**. A
feature that was never built cannot be broken, but "URGENT" outranked the rubric. Cases 9 and
13 inflated P2 to P1 the same way.

**c. Task interference.** One call classifies, extracts, *and* drafts an empathetic response.
Drafting an apology biases the severity judgement it is supposed to be conditioned on — the
model reaches for a priority that justifies the tone it has already started writing in.

**d. No contract for the unknown.** `If unsure about priority, use your best judgment`
licenses precisely the guessing your eval punishes. Cases 7 and 21 were over-triaged to P2 on
vague input. A vague ticket has a correct answer, and it is "P3, low confidence, ask a
question" — not a guess.

**Failure distribution at baseline (6/21):** of the 15 failing cases, 12 fail on
`entities_accurate`, 6 on `priority_correct`, 1 on `json_valid` (case 6), and 1 case is
unscoreable because the JSON did not parse. Entity hallucination is the dominant defect by a
wide margin.

## 2. What we changed

One monolithic call became a **two-step chain with a bounded repair pass**. Both prompts were
authored from a written spec (`prompt-bundle/step1-triage/`, `prompt-bundle/step2-draft/`),
so every constraint traces back to a named failure mode.

- **Step 1 — triage.** Priority from impact only, with six ordered tie-breakers that name the
  defects above: *tone is not evidence*; *a feature request is P4 however urgently it is
  phrased*; *data exposure is P1 on severity alone*; *a broken feature is not an outage*;
  *multi-issue takes the most severe*; *unestablishable impact is P3 with low confidence*.
  Plus the one rule that does most of the work: **every entity value is a span copied
  character-for-character out of the ticket, or it is `null`. A hedge is a hallucination** —
  with the baseline's own hedge strings listed as wrong answers.
- **Step 2 — draft and assemble.** The customer response, conditioned on step 1's verdict, then
  the final JSON. Priority, entities and confidence pass through unchanged, so drafting can no
  longer move the classification.

**Two steps, not three.** Your constraint is a 5-second budget and a written rule that more
than three sequential calls needs justifying. Two base calls plus at most one repair lands at
three worst case, two typically. A third "validate" LLM call would buy a model opinion where a
`str.find()` does the job for free.

**The repair pass, and the line we did not cross.** After each step a pure-Python validator
runs over the model's own output: does the JSON parse; is every non-null entity value actually
present in the ticket; does a P4 response avoid promising a fix; does a low-confidence response
ask a question. A failing step is re-run once with the complaint attached, capped at three
calls per ticket.

That validator is deliberately blind to the answer key. It never reads `gold_priority` or
`gold_entities` — it sees only the ticket text and the model's own output, which is exactly
what production has. A retry loop conditioned on the labels would score beautifully and measure
nothing. It is the same loop in production and in the eval, because it needs nothing production
lacks.

## 3. Results

| Run | Configuration | Score |
|---|---|---|
| 0 | Baseline — the prompt as shipped | **6/21 (29%)** |
| 1 | v1 — single prompt, authored from a spec | 20–21/21 |
| 2 | v2 — two-step chain, healing off | 20–21/21 |
| 3 | v3 — chain + self-healing repair pass | 20–21/21 |
| 4 | v3 again, identical configuration | 20–21/21 |
| — | Claude one-shot rewrite, no diagnosis (control) | **2–3/21 (10–14%)** |

Ranges are across the two full executions. Category breakdown at the end state: clean inputs
4/4, multi-issue 2/2, vague/unclear 3/3, non-native English 3/3, feature requests 1–2/2,
complex/long/edge 7/7.

**Three things in that table matter more than the headline number.**

**The chain did not earn the score — the rubric and the grounding rule did.** The single
spec-driven prompt already scores 20–21/21. Decomposition and self-healing move it by at most
one case, which is inside the noise (below). Had we run only the chain and reported the delta
from baseline, we would have credited the wrong technique for the entire gain. The one-
variable-per-run sequence exists to prevent exactly that.

**The repair pass fired zero times in 168 tickets.** Not once across eight full-suite runs.
That is the right outcome — the prompts do not produce output the validator rejects — and it is
also indistinguishable from a loop that is wired up wrong. So the notebook carries a negative
control: a stub that hallucinates on purpose, asserted to trigger exactly one repair, carry the
correct complaint (`entities.product = "Reporting Suite" does not appear in the ticket text`),
and stop at the 3-call cap. The loop works; it simply has nothing to catch here. It is tail-risk
insurance for production, not a scorer.

**Latency: the chain has no headroom.** Measured per ticket, the chain runs p50 3.65–3.90s and
p95 4.46–5.83s against your 5-second budget, at a flat 2.00 API calls per ticket. Seven of the
eight runs came in under budget at p95; **one breached it** (p95 5.83s, max 12.85s) on ordinary
API variance, with no repair calls involved. The single prompt completes the whole 21-case suite
in 46–59s versus 83–95s for the chain — the second call roughly doubles per-ticket time.

**So the recommendation is the unglamorous one:** ship the single spec-driven prompt now. It
scores the same, at half the cost and with real latency headroom. Keep the chain in your
repository for the moment step 2 gains work that genuinely needs isolation from the
classification — routing, escalation, anything that would re-introduce task interference. The
validator and repair loop are written to drop onto either shape unchanged.

**A note on the control run.** We asked Claude to fix the original prompt with no diagnosis and
no eval feedback — the counterfactual for "would the methodology have mattered?". It scored
2–3/21, *below the broken baseline*, because it wrote longer, more helpful-sounding entity
values: `affected_users="~500 users (entire organization)"`, `product="unclear - appears to be a
reporting/business system"`. Fluency without a measurement moved the number the wrong way. The
eval loop is the deliverable, not the prompt.

## 4. Prevention — what your harness will not catch

We ran an evaluation-hygiene audit over *your* harness, not ours. Verbatim:

```
[warning] PI003 harness.py:428  two runs are compared with nothing checking they are comparable — a delta across a changed corpus, judge or metric definition is a re-baseline wearing a delta's clothes; read the fingerprint back before subtracting, and say so when it does not match
[warning] PI007 harness.py:217  no grader is paired with an input it is proven to score as failing — a grader never observed to fail is not a measurement, and format checks degrade to a free pass in ways that are invisible until someone tries it (a regex validator accepts almost any string, including a refusal); commit a failing fixture per grader and assert it
[warning] PI011 harness.py:0  the harness scores each case once and reports the mean of a single sample — a nondeterministic system sampled once gives a number with unknown error bars, and a delta smaller than the run-to-run spread is not a result; report mean with standard deviation or standard error
[info   ] PI012 harness.py:278  an output cap is set but stop_reason is never inspected — a truncated answer fails a syntax or format grader for a reason that has nothing to do with quality, and it lands in the score as if the model had answered badly; check the stop reason and count truncations and API errors separately from low scores
[warning] PI013 harness.py:237  the judge grades each case against its own criteria, but nothing hashes or versions those criteria — edit one criterion and every past score silently describes a different question, with the inputs and the prompt both unchanged, so the run records still claim to be comparable

summary: 5 finding(s) — 0 error, 4 warning, 1 info; 3/8 applicable controls present, 6 rule(s) not applicable
```

Four of those matter to you today.

**The judge fails open.** `judge_response` returns `True` when the judge's reply cannot be
parsed *and* when the API call raises — `return True, f"Judge call failed (defaulting to
PASS): {e}"`. An outage during a nightly eval does not surface as a broken run; it surfaces as
a perfect `response_coherent` score. Nothing on your dashboard would distinguish that from a
good release. Fail closed, and count API errors separately from low scores.

**No grader is proven able to fail.** None of your four checks is paired with a committed input
it is known to reject. We added one for our own validator (cell 19) and it caught a real
question — "is this loop even wired up?" — that eight clean runs could not answer. Do the same
per grader: one failing fixture, committed, asserted.

**Every case is scored once, and the spread is wider than the effects you are measuring.** Two
identical runs of the same configuration disagreed with each other (20/21 vs 21/21). The same
v1 prompt scored 20/21 and 21/21 in different runs of the same session. In every instance the
difference was case 11's LLM judge sampling a different verdict on materially the same
response. Your `compare_prompts` subtracts two such numbers and prints the delta in a coloured
box. Report a mean and a spread, and treat any delta smaller than the spread as noise.

**The judge's criteria are not versioned with the corpus.** Edit one criterion and every
historical score silently answers a different question while the run records still claim to be
comparable. Freeze the criteria with the corpus; treat a criteria edit as a new baseline.

### And one the audit cannot see

Reading `score_case` and `check_entities` directly:

1. `check_entities(parsed, input_text, gold_entities)` takes `gold_entities` **and never uses
   it**. Entity scoring is a pure hallucination check: every non-empty value must be derivable
   from the ticket. There is no recall term.
2. `response_coherent` auto-passes on every case not marked `audited` — `return True,
   "Auto-pass (non-audited case)"`. Only cases 5, 8, 11, 17 and 19 are judged at all.

Together those mean **a prompt that returns `null` for every entity on every ticket scores full
marks on entity accuracy.** That is a live hole, and it is the exact shape of a metric a
well-meaning team optimises straight into. We did not exploit it — step 1 says "copy the span",
not "return null when unsure" — but the next person to tune against this score will, and their
number will look excellent while your triage queue loses the product names it runs on.

The fix is small: add a recall term over `gold_entities`, and mark more cases `audited` so the
response grader carries weight beyond five tickets.

---

## 5. Three questions to ask of any prompt before it ships

1. **Does every field have a defined value for the unknown case?** If "leave it out" is not
   written down, the model will write prose into it. This single rule accounts for most of the
   14-case gain above.
2. **Is any instruction conditioning on how the input is phrased rather than what it says?**
   Tone, urgency and capitals are input features. Say explicitly which ones count as evidence.
3. **Can your grader be shown failing?** If no committed input makes it return FAIL, you do not
   have a measurement — you have a green light.

---

## Artifacts

| Path | What it is |
|---|---|
| `Prompt_Rescue_solo.ipynb` | The worked exercise, executed end to end with real outputs |
| `prompt-bundle/step1-triage/` | Step 1 spec, prompt and authoring notes |
| `prompt-bundle/step2-draft/` | Step 2 spec, prompt and authoring notes |
| `prompt-bundle/v1-single/` | The single-prompt variant, for the A/B attribution |

Prompts were drafted and linted with `skill-prompt-authoring` (PD001–PD014, all three bundles
clean); the harness audit is `prompt-evaluation-best-practices` (PI001–PI014); the diagnosis
follows the `skill-diagnose` reproduce → minimise → hypothesise → instrument → fix →
regression-test loop.
