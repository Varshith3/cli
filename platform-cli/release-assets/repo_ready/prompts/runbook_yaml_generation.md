You are generating the canonical `.ghdp/runbook.yaml` file for GHDP repo readiness.

Your job is to produce a strict YAML document that captures machine-readable developer commands and runtime prerequisites for this repository, without inventing commands.

Inputs you may receive:
- Repository name
- Existing `.ghdp/runbook.yaml` content, if any
- Repo scan summary (tree + key files)
- Evidence sources (when available): README snippets, CI workflows, Makefile targets, package.json scripts, build manifests (pom.xml, build.gradle, pyproject.toml, etc.)
- Optional `.ghdp/frbr/intent.json`

Generation rules:
- Return only YAML. Do not return markdown, prose, explanations, comments, or code fences.
- Do not invent commands. Only include commands that are supported by evidence from repo contents (scripts, Makefile, CI, docs, manifests).
- If evidence for a command is missing, omit that command entirely (do not guess).
- Preserve existing valid commands if already present and still supported by evidence.
- Prefer minimal, high-signal content over long lists.

Content requirements:
- Include commands where evidence exists:
  - build
  - lint
  - format
  - test (only if evidence exists; GHDP may run tests via other capabilities, but runbook should still document how tests are run when it is known)
  - start / dev (if applicable)
- For each command you include, provide:
  - `cmd`: the exact shell command
  - `cwd`: working directory (repo root or subdir), only if required
  - `notes`: short, factual notes only when necessary (e.g., "requires docker")
- Include `entrypoints` if evidence exists (main service entry file, pipeline entry script, app bootstrap).
- Include `services` only when evidence exists:
  - name (db/redis/localstack/etc.)
  - how it is started (only if documented in repo)
- Include `env_vars` as NAMES only (no values). Include only env var names you can find in repo evidence (docs, .env.example, CI, code references). Do not invent env vars.

Safety:
- Never include secret values.
- Never include tokens, credentials, account ids, or endpoints that look sensitive.

Output target:
- The final output must be a complete `.ghdp/runbook.yaml` document that GHDP can write directly after user review.
