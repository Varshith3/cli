# Phase 1 Agent Inventory

This file is the human-readable companion to `.ghdp/agents/manifest.json`.

All Phase 1 agents are repo-level contracts. The manifest is the index. Each agent now has its own simple contract file under `.ghdp/agents/<agent-id>.json`.

## How To Read This Folder

- Use `.ghdp/agents/manifest.json` as the machine-readable index.
- Open `.ghdp/agents/<agent-id>.json` for the actual agent contract.
- Each agent contract explicitly declares:
  - `allowed_skills`
  - `allowed_plugins`
  - `stages_owned`
  - `approval_mode`
  - `prompt_contract`
- Runtime state belongs under `.ghdp/orchestrate/`, not here.

## Agent Contract Files

- `.ghdp/agents/orchestrator.json`
- `.ghdp/agents/ticket-intake.json`
- `.ghdp/agents/work-type-classifier.json`
- `.ghdp/agents/autonomy-assessor.json`
- `.ghdp/agents/context-capability-discovery.json`
- `.ghdp/agents/asset-lifecycle.json`
- `.ghdp/agents/parallel-work-awareness.json`
- `.ghdp/agents/blueprint-planner.json`
- `.ghdp/agents/architecture-review.json`
- `.ghdp/agents/ux-dx-review.json`
- `.ghdp/agents/implementation.json`
- `.ghdp/agents/qa-scenario-design.json`
- `.ghdp/agents/regression-validation.json`
- `.ghdp/agents/test-coverage-authoring.json`
- `.ghdp/agents/developer-test-execution.json`
- `.ghdp/agents/binary-validation.json`
- `.ghdp/agents/release-readiness.json`
- `.ghdp/agents/release-prerelease.json`
- `.ghdp/agents/published-prerelease-validation.json`
- `.ghdp/agents/pr-external-integration.json`
- `.ghdp/agents/traceability-historian.json`

## Design Notes

- Keep this layer simple: one index plus one file per agent.
- Put explicit skill/plugin access in the agent contract itself.
- Do not bury access rules in runtime code when the repo contract can say it directly.
- Treat asset-only work as a first-class path. Do not force every capability-asset revision through the full SDLC if the request is really just asset lifecycle management.
