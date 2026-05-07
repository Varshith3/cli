You are generating the repo-local `AGENTS.md` adapter for GHDP repo readiness.

Your job is to produce a thin Markdown adapter for Codex and other AGENTS.md-aware coding agents that points back to canonical `.ghdp/*` files without duplicating large policy or architecture bodies.

Inputs you may receive:
- Repository name
- Current branch name
- Existing `AGENTS.md`, if any
- Canonical GHDP files such as:
  - `.ghdp/readiness.json`
  - `.ghdp/architecture.md`
  - `.ghdp/runbook.yaml`
  - `.ghdp/config.yaml`
  - `.ghdp/guardrails.yaml`
  - `.ghdp/lock.yaml`
  - optional `.ghdp/frbr/intent.json`

Generation rules:
- Return only Markdown. Do not wrap the output in code fences.
- Keep this file thin and operational.
- Treat `.ghdp/*` as the source of truth.
- Do not duplicate long sections from `.ghdp/architecture.md` or `.ghdp/guardrails.yaml`.
- Do not invent commands, repo boundaries, or policies not supported by the GHDP files.
- If a canonical GHDP file is missing, say it is missing and tell the reader to rely on the remaining GHDP files instead of guessing.
- Mention the GHDP read order clearly:
  1. `.ghdp/frbr/intent.json` when present
  2. `.ghdp/readiness.json` when present
  3. `.ghdp/architecture.md`
  4. `.ghdp/runbook.yaml`
  5. `.ghdp/config.yaml`
  6. `.ghdp/guardrails.yaml`
  7. `.ghdp/lock.yaml`
- Keep the file concise and maintainable.

Suggested structure:
- `# Agent Instructions`
- short statement that `.ghdp/*` is canonical
- `## Read Order`
- `## Working Rules`
- `## Validation`
- `## Notes`

Working Rules guidance:
- do not invent missing GHDP content
- prefer readiness gaps before unrelated work when `.ghdp/readiness.json` is present
- ask the user when missing GHDP context blocks safe progress

Validation guidance:
- point to `.ghdp/runbook.yaml` and `.ghdp/readiness.json`
- keep it short

Output target:
- The final output must be a complete `AGENTS.md` body that GHDP can wrap in its managed adapter block.
