# Prompt notes

- measured: false
- kind: system
- mode: draft
- spec: prompt-spec.json
- drafted_by: skill-prompt-authoring

## What this is

The system prompt for an agent or subagent. Process an inbound support ticket end to end: assign a P1-P4 priority, extract only the entities the ticket states, and draft the customer response.

## Applied

- role assignment — what expertise the agent answers from
- rung 2 — an explicit output contract
- an obstacle clause — what to do when the work cannot be done
- rung 3 — XML tags around anything interpolated

## Measured

**No.** Nothing here has been scored against a corpus, so treat every claim about how well it works as untested. Seed a corpus and record a baseline before trusting one:

```
python3 scripts/prompt_iterate.py generate \
  --task "Process an inbound support ticket end to end: assign a P1-P4 priority, extract only the entities the ticket states, and draft the customer response" \
  --input ticket="the raw inbound support ticket text" \
  --cases 20 --out corpus.json
```

When a graded run exists, set `measured: true` above and add `graded_run: <path>` beside it. PD013 fires on a claim with no run behind it.

## Next

agents-best-practices for the harness, llm-security-best-practices for the trust boundary
