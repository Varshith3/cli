# Phase 1 Orchestrate Runtime

This folder contains branch-scoped runtime state for the Phase 1 orchestrator.

## What Belongs Here

- branch-level canonical POA
- branch controller state
- handoff summaries
- run-scoped decisions and resume context

## What Does Not Belong Here

- static agent, skill, plugin, or memory contracts
- noisy machine-local execution logs
- user-global locks and heartbeats
- finalized branch runtime folders after merge-hygiene closeout

Those belong in:
- `.ghdp/agents/`
- `.ghdp/skills/`
- `.ghdp/plugins/`
- `.ghdp/memory/`
- `~/.ghdp/`

## Merge Hygiene

Runtime folders under `.ghdp/orchestrate/branches/<branch>/...` are feature-branch working state, not durable repo memory.

Before merge:
- run `ghdp orchestrate finalize`
- commit the promoted closeout summary and receipt
- run `ghdp orchestrate verify-merge-hygiene`

The durable record should live under `.ghdp/memory/shared/`, while the runtime folder itself should be archived and pruned.
