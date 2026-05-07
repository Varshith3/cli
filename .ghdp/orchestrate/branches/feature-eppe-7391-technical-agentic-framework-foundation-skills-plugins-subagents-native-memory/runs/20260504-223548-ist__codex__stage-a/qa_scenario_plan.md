# QA Scenario Plan

- Status: `designed`
- Owner agent: `qa-scenario-design`
- Scenario count: `10`

## Acceptance Anchors
- skills, plugins, and sub-agent orchestration are usable through a consistent framework path
- native memory handling works for the baseline agentic flow without external memory dependencies
- framework docs clearly separate current baseline capabilities from later EPPE-7581 integration work

## Scenarios
- Validate the happy-path orchestrator run for `EPPE-7391` on `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory` from `orchestrate start` through `commit-push` without losing `.ghdp/orchestrate` state.
- Acceptance scenario 1: confirm `skills, plugins, and sub-agent orchestration are usable through a consistent framework path` with repo-backed artifacts and CLI-visible state transitions.
- Acceptance scenario 2: confirm `native memory handling works for the baseline agentic flow without external memory dependencies` with repo-backed artifacts and CLI-visible state transitions.
- Acceptance scenario 3: confirm `framework docs clearly separate current baseline capabilities from later EPPE-7581 integration work` with repo-backed artifacts and CLI-visible state transitions.
- Target coverage: change or inspect `.ghdp/agents/manifest.json` and verify the orchestrator can still explain the touched scope through the POA and runtime artifacts.
- Target coverage: change or inspect `.ghdp/memory/context/README.md` and verify the orchestrator can still explain the touched scope through the POA and runtime artifacts.
- Target coverage: change or inspect `.ghdp/memory/manifest.json` and verify the orchestrator can still explain the touched scope through the POA and runtime artifacts.
- Commit/push continuity: confirm Stage 12 committed branch artifacts remain understandable when Stage 13 rewrites QA artifacts afterward.
- Failure-path scenario: simulate a missing or stale run artifact and confirm the next stage can identify the gap before running tests.
- Resume-path scenario: pause after QA design, reopen the branch later, and confirm `resume_context.md` still points the next owner to regression and coverage work.

## Edge Cases
- The active branch has a valid run key but stale Stage 12 artifacts from an older checkpoint.
- The QA scenario plan is regenerated after new runtime files are added and must stay deterministic.
- Touched-scope regression focuses only on orchestrate files while a missed `.ghdp` artifact mutation slips in.
- Coverage goals drift into broad churn instead of staying proportional to the current branch change.
- At least one scenario must explicitly validate runtime behavior around `.ghdp/agents/manifest.json`.

