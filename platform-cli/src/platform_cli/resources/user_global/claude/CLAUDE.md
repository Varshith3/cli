## GHDP Global Rules

When working inside any repository:

- Locate the repo root and check for `.ghdp/`.
- If `.ghdp/` exists, consult GHDP files in this order:
  1. `.ghdp/frbr/intent.json` when present
  2. `.ghdp/readiness.json` when present
  3. `.ghdp/architecture.md`
  4. `.ghdp/runbook.yaml`
  5. `.ghdp/config.yaml`
  6. `.ghdp/guardrails.yaml`
  7. `.ghdp/lock.yaml`
- If `.ghdp/readiness.json` exists, use it to prioritize readiness gaps before unrelated work.
- If a GHDP file is missing, do not invent its contents.
- If missing GHDP context blocks safe progress, ask the user.
- If `.ghdp/` is absent, proceed conservatively and optionally suggest running GHDP repo readiness.
