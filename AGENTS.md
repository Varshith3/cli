<!-- GHDP:BEGIN MANAGED BLOCK -->
generated_by: ghdp
adapter_path: AGENTS.md
source_of_truth: .ghdp/*
warning: Do not edit the managed block by hand; update .ghdp contracts instead.
<!-- GHDP:END MANAGED BLOCK -->

# Repo Bootstrap

This repository uses `.ghdp/` as the source of truth for agentic orchestration.

## Read Order

When working in this repo, read GHDP context in this order:

1. `.ghdp/frbr/intent.json`
2. `.ghdp/agents/manifest.json`
3. `.ghdp/skills/manifest.json`
4. `.ghdp/plugins/manifest.json`
5. `.ghdp/orchestrate/kernel.json`
6. `.ghdp/orchestrate/topology.json`
7. `.ghdp/orchestrate/stages/manifest.json`
8. `.ghdp/orchestrate/scenarios/manifest.json`
9. `.ghdp/memory/README.md`

## Operating Model

- Treat `.ghdp` as the repo-owned orchestration brain.
- Treat the execution kernel as the scheduler/executor that honors the `.ghdp` contracts.
- Do not invent parallel or sequential execution rules outside `.ghdp/orchestrate/topology.json`.
- Do not invent skill or plugin access outside `.ghdp/agents/<agent-id>.json`.
- Do not treat hidden chat memory as required context when repo artifacts can carry the state.

## Work Types

This repo has distinct lifecycle paths for:

- `new_feature`
- `enhancement`
- `bug_fix`
- `maintenance`
- `asset_only`

Route work through the repo-defined work-type classifier and front-door gates before expanding implementation.

## Default Behavior

For any substantial development, enhancement, or fix:

- use the repo-defined orchestrator path
- use repo-defined sub-agents from `.ghdp/agents/`
- use repo-defined skills from `.ghdp/skills/`
- use repo-defined plugins from `.ghdp/plugins/`
- honor repo-defined stage flow under `.ghdp/orchestrate/stages/`
- write and update runtime evidence under `.ghdp/orchestrate/`

For requests that are really about revising existing GHDP-managed assets:

- prefer the lightweight asset lifecycle path
- use `.ghdp/agents/asset-lifecycle.json`
- use `.ghdp/skills/asset-capability-discovery/SKILL.md`
- use `.ghdp/skills/asset-lifecycle-operations/SKILL.md`
- use `.ghdp/plugins/asset-lifecycle-sync/plugin.json`
- do not force full SDLC unless broader code, behavior, or release work is actually needed

## Host Adapters

Host-specific bootstrap instructions live in:

- `.codex/AGENTS.md`
- `.claude/AGENTS.md`

Those files should adapt the same `.ghdp` source of truth to the host runtime. They are not alternate orchestration systems.
