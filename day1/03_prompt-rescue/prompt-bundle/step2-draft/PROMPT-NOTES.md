# Prompt notes

- measured: false
- kind: system
- mode: draft
- spec: prompt-spec.json
- drafted_by: skill-prompt-authoring

## What this is

The system prompt for an agent or subagent. Write the customer response for an already-triaged support ticket and assemble the final triage record.

## Applied

- role assignment — what expertise the agent answers from
- rung 2 — an explicit output contract
- an obstacle clause — what to do when the work cannot be done
- rung 3 — XML tags around anything interpolated

## Measured

**No.** Nothing here has been scored against a corpus, so treat every claim about how well it works as untested. Seed a corpus and record a baseline before trusting one:

```
python3 scripts/prompt_iterate.py generate \
  --task "Write the customer response for an already-triaged support ticket and assemble the final triage record" \
  --input ticket="the raw inbound support ticket text" \
  --input triage="the triage record produced by the previous step" \
  --cases 20 --out corpus.json
```

When a graded run exists, set `measured: true` above and add `graded_run: <path>` beside it. PD013 fires on a claim with no run behind it.

## Next

agents-best-practices for the harness, llm-security-best-practices for the trust boundary
