# EPPE-7391 POA: Phase 1 Agentic Orchestrator Foundation

## Goal

Implement the Phase 1 agentic orchestrator foundation inside GHDP so repo-level skills, plugins, sub-agents, and native memory handling work through one consistent orchestration framework.

## Intent Reference

- Ticket: `EPPE-7391`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Repo intent: `.ghdp/frbr/intent.json`
- Source of truth: `phase1_agentic_orchestrator_ground_truth.md`

## Current Implementation Stages

### Stage A: Contract Foundation
- create repo-level `.ghdp/agents`, `.ghdp/skills`, `.ghdp/plugins`, and `.ghdp/memory` contracts
- create the initial branch-scoped orchestrator runtime skeleton
- add a CLI inspection path so the contract is executable and testable today

### Stage B: Core Orchestrator Runtime
- implement run creation, policy load, stage transitions, and branch/run artifact updates

### Stage C: Front-Door Agent Gates
- intake sufficiency
- work-type classification
- autonomy assessment
- context/capability discovery
- parallel work awareness
- canonical POA authoring

### Stage D: Review Layer
- architecture review
- UX/DX review

### Stage E: Execution, Testing, and Release Path
- implementation
- regression protection
- new coverage authoring
- developer test execution
- artifact validation
- prerelease and PR/Jira integration

## Acceptance Mapping

This branch is successful when:

1. `.ghdp` contains the frozen Phase 1 contract structure.
2. The CLI can inspect and validate the orchestrator contract.
3. The current branch has branch-scoped runtime artifacts under `.ghdp/orchestrate/branches/...`.
4. Later stages can build on these contracts without redesigning the storage model.

## Watchpoints

- Do not mix static capability contracts with runtime state.
- Keep machine-local noise out of repo `.ghdp`.
- Keep review personas and testing personas as first-class concepts, not optional notes.
- Keep sync awareness minimal but explicit in Phase 1.

<!-- GHDP:BEGIN STAGE_C_FRONT_DOOR -->
## Stage C Front-Door Gate Outputs

- Ticket: `EPPE-7391`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Work type: `new_feature`
- Intake sufficient: `True` at confidence `0.99`
- Autonomy level: `semi_autonomous` at confidence `0.84`
- Spec action: `create_new_spec`
- Parallel work decision: `proceed`

### Capability Matches
- `context-capability-discovery`
- `folder-backed-shared-memory`
- `native-memory-filesystem`
- `orchestrator`
- `repo-local-code-context`
- `provider-claude`
- `provider-codex`
- `regression-validation`
- `parallel-work-awareness`
- `qa-scenario-design`
- `qa-scenario-generation`
- `architecture-review`

### Impacted Areas
- `.ghdp/agents/manifest.json`
- `.ghdp/memory/context/README.md`
- `.ghdp/memory/manifest.json`
- `.ghdp/memory/shared/README.md`
- `.ghdp/plugins/manifest.json`
- `.ghdp/skills/manifest.json`
- `platform-cli/src/platform_cli/commands/orchestrate.py`
- `platform-cli/src/platform_cli/tools/orchestrate_front_door.py`
- `platform-cli/src/platform_cli/tools/orchestrate_runtime.py`

<!-- GHDP:END STAGE_C_FRONT_DOOR -->

<!-- GHDP:BEGIN STAGE_D_REVIEW -->
## Stage D Review Findings

### Architecture Review
- ACCEPTED: Repo-level capability contracts remain separated from runtime state under `.ghdp/orchestrate/`.
- ACCEPTED: Manifest loading and validation continue to live under `src/platform_cli/manifests/`, which aligns with the repo architecture rules.
- RESIDUAL_RISK: Capability discovery is still heuristic and may need tightening once more Stage E implementation history exists.

### UX/DX Review
- ACCEPTED: The orchestrate command surface remains explicit and human-readable across status, start, resume, handoff, and front-door flows.
- ACCEPTED: Repo-local POA, handoff, and resume artifacts provide enough operator context for pause/resume without hidden session memory.
- RESIDUAL_RISK: Stage-by-stage commands are still verbose for end users until a higher-level orchestrate run path exists.

<!-- GHDP:END STAGE_D_REVIEW -->


<!-- GHDP:BEGIN STAGE_E_EXECUTION -->
## Stage E Execution Prep Outputs

- Work type: `new_feature`
- Implementation target count: `9`
- Regression target count: `4`
- Coverage goal count: `3`

### Bound Skills
- `traceability-and-resume`
- `qa-scenario-generation`
- `touched-scope-regression`
- `test-coverage-authoring`
- `developer-test-execution`
- `isolated-binary-validation`
- `architecture-compliance`
- `release-and-pr`
- `stable-release-notes-assembly`
- `jira-acli-integration`
- `folder-backed-shared-memory`

### Bound Plugins
- `provider-codex`
- `provider-claude`
- `native-memory-filesystem`
- `github-release-gh`
- `jenkins-mcp`
- `jira-acli`

### Implementation Targets
- `.ghdp/agents/manifest.json`
- `.ghdp/memory/context/README.md`
- `.ghdp/memory/manifest.json`
- `.ghdp/memory/shared/README.md`
- `.ghdp/plugins/manifest.json`
- `.ghdp/skills/manifest.json`
- `platform-cli/src/platform_cli/commands/orchestrate.py`
- `platform-cli/src/platform_cli/tools/orchestrate_front_door.py`
- `platform-cli/src/platform_cli/tools/orchestrate_runtime.py`

### Regression Targets
- `platform-cli/tests/test_orchestrate_contract.py`
- `platform-cli/tests/test_orchestrate_runtime.py`
- `platform-cli/tests/test_orchestrate_front_door.py`
- `platform-cli/tests/test_orchestrate_review.py`

### Coverage Goals
- Add or extend tests for any new Stage E command surface and runtime artifact writer.
- Ensure branch runtime artifact mutations remain deterministic in repo-backed runs.
- Protect the manifest/tool layering assumptions introduced in earlier stages.

<!-- GHDP:END STAGE_E_EXECUTION -->


<!-- GHDP:BEGIN STAGE11_IMPLEMENTATION -->
## Stage 11 Implementation Activation

- Implementation agent: `implementation`
- Allowed skill count: `1`
- Allowed plugin count: `3`
- Target count: `9`

### Allowed Skills
- `traceability-and-resume`

### Allowed Plugins
- `provider-codex`
- `provider-claude`
- `native-memory-filesystem`

### Active Targets
- `.ghdp/agents/manifest.json`
- `.ghdp/memory/context/README.md`
- `.ghdp/memory/manifest.json`
- `.ghdp/memory/shared/README.md`
- `.ghdp/plugins/manifest.json`
- `.ghdp/skills/manifest.json`
- `platform-cli/src/platform_cli/commands/orchestrate.py`
- `platform-cli/src/platform_cli/tools/orchestrate_front_door.py`
- `platform-cli/src/platform_cli/tools/orchestrate_runtime.py`

<!-- GHDP:END STAGE11_IMPLEMENTATION -->


<!-- GHDP:BEGIN STAGE12_COMMIT_PUSH -->
## Stage 12 Commit Push

- Commit message: `[EPPE-7391] Orchestrator implementation checkpoint (5 files)`
- Head SHA: `resolved_after_commit`
- Files committed: `5`

### Files Committed
- `platform-cli/00_GHDP_CLI_COMMANDS_REFERENCE.toml`
- `platform-cli/AGENT_ORCHESTRATION_JOURNEY.md`
- `platform-cli/src/platform_cli/commands/orchestrate.py`
- `platform-cli/src/platform_cli/tools/orchestrate_commit_push.py`
- `platform-cli/tests/test_orchestrate_commit_push.py`

<!-- GHDP:END STAGE12_COMMIT_PUSH -->


<!-- GHDP:BEGIN STAGE13_QA_SCENARIOS -->
## Stage 13 QA Scenario Design

- QA agent: `qa-scenario-design`
- Allowed skill count: `1`
- Allowed plugin count: `2`
- Scenario count: `10`
- Edge-case count: `5`

### Allowed Skills
- `qa-scenario-generation`

### Allowed Plugins
- `provider-codex`
- `provider-claude`

<!-- GHDP:END STAGE13_QA_SCENARIOS -->


<!-- GHDP:BEGIN STAGE14_REGRESSION -->
## Stage 14 Touched Scope Regression

- Regression agent: `regression-validation`
- Allowed skill count: `1`
- Allowed plugin count: `2`
- Selected regression test count: `6`

### Allowed Skills
- `touched-scope-regression`

### Allowed Plugins
- `provider-codex`
- `provider-claude`

### Selected Tests
- `platform-cli/tests/test_orchestrate_contract.py`
- `platform-cli/tests/test_orchestrate_runtime.py`
- `platform-cli/tests/test_orchestrate_front_door.py`
- `platform-cli/tests/test_orchestrate_review.py`
- `platform-cli/tests/test_orchestrate_manifests.py`
- `platform-cli/tests/test_orchestrate_qa.py`

<!-- GHDP:END STAGE14_REGRESSION -->


<!-- GHDP:BEGIN STAGE15_COVERAGE -->
## Stage 15 New Test Coverage

- Coverage agent: `test-coverage-authoring`
- Allowed skill count: `1`
- Allowed plugin count: `2`
- Authored test count: `5`

### Allowed Skills
- `test-coverage-authoring`

### Allowed Plugins
- `provider-codex`
- `provider-claude`

### Authored Test Backlog
- `platform-cli/tests/test_orchestrate_execution.py`
- `platform-cli/tests/test_orchestrate_coverage.py`
- `platform-cli/tests/test_orchestrate_regression.py`
- `platform-cli/tests/test_orchestrate_contract.py`
- `platform-cli/tests/test_orchestrate_manifests.py`

<!-- GHDP:END STAGE15_COVERAGE -->


<!-- GHDP:BEGIN STAGE16_TEST_EXECUTION -->
## Stage 16 Developer Test Execution

- Execution agent: `developer-test-execution`
- Allowed skill count: `1`
- Allowed plugin count: `1`
- Execution mode: `sequential`
- Executed test count: `9`

### Allowed Skills
- `developer-test-execution`

### Allowed Plugins
- `native-memory-filesystem`

### Executed Tests
- `platform-cli/tests/test_orchestrate_contract.py`
- `platform-cli/tests/test_orchestrate_runtime.py`
- `platform-cli/tests/test_orchestrate_front_door.py`
- `platform-cli/tests/test_orchestrate_review.py`
- `platform-cli/tests/test_orchestrate_manifests.py`
- `platform-cli/tests/test_orchestrate_qa.py`
- `platform-cli/tests/test_orchestrate_execution.py`
- `platform-cli/tests/test_orchestrate_coverage.py`
- `platform-cli/tests/test_orchestrate_regression.py`

<!-- GHDP:END STAGE16_TEST_EXECUTION -->


<!-- GHDP:BEGIN STAGE17_BINARY_VALIDATION -->
## Stage 17 Packaged Artifact Validation

- Validation agent: `binary-validation`
- Allowed skill count: `1`
- Allowed plugin count: `1`
- Package root: `C:\Users\Hi\Downloads\git-repos\dp-tools-local-setup\platform-cli`
- Installed CLI version: `ghdp 0.0.0 (beta)`

### Allowed Skills
- `isolated-binary-validation`

### Allowed Plugins
- `native-memory-filesystem`

<!-- GHDP:END STAGE17_BINARY_VALIDATION -->


<!-- GHDP:BEGIN STAGE18_RELEASE_READINESS -->
## Stage 18 Release Readiness

- Readiness agent: `release-readiness`
- Allowed skill count: `2`
- Allowed plugin count: `3`
- Blocking finding count: `0`

### Allowed Skills
- `architecture-compliance`
- `traceability-and-resume`

### Allowed Plugins
- `provider-codex`
- `provider-claude`
- `native-memory-filesystem`

### Blocking Findings
- None.

<!-- GHDP:END STAGE18_RELEASE_READINESS -->


<!-- GHDP:BEGIN STAGE19_PRERELEASE -->
## Stage 19 Prerelease Creation

- Prerelease agent: `release-prerelease`
- Allowed skill count: `2`
- Allowed plugin count: `3`
- Planned tag: `v1.0.6-AgenticFrameworkFoundationSkillsPluginsSubagentsNativeMemory`
- Blocked reason: `none`

### Allowed Skills
- `release-and-pr`
- `stable-release-notes-assembly`

### Allowed Plugins
- `github-release-gh`
- `github-pr-gh`
- `jenkins-mcp`

<!-- GHDP:END STAGE19_PRERELEASE -->

<!-- GHDP:BEGIN STAGE20_RELEASE_NOTES -->
## Stage 20 Release Notes Refresh
- Owner agent: `release-prerelease`
- Allowed skills: `release-and-pr`, `stable-release-notes-assembly`
- Allowed plugins: `github-release-gh`, `github-pr-gh`, `jenkins-mcp`
- Notes path: `.github/release-notes/notes.md`
- Freshness commit: `9314c431eed3f92043076e605081840d5b57a341`
- Blocked reason: `none`
<!-- GHDP:END STAGE20_RELEASE_NOTES -->

<!-- GHDP:BEGIN STAGE21_PR_INTEGRATION -->
## Stage 21 PR and External Integration
- Owner agent: `pr-external-integration`
- Allowed skills: `release-and-pr`, `jira-acli-integration`
- Allowed plugins: `jira-acli`, `github-pr-gh`, `github-release-gh`
- PR link: `https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/64`
- Jira ticket: `EPPE-7391`
- Blocked reason: `none`
<!-- GHDP:END STAGE21_PR_INTEGRATION -->

<!-- GHDP:BEGIN STAGE22_HISTORIAN -->
## Stage 22 Traceability Capture
- Owner agent: `traceability-historian`
- Allowed skills: `traceability-and-resume`, `folder-backed-shared-memory`
- Allowed plugins: `native-memory-filesystem`
- Scenario packet present: `yes`
- Executed sub-agents: `8`
<!-- GHDP:END STAGE22_HISTORIAN -->

<!-- GHDP:BEGIN ASSET_LIFECYCLE -->
## Asset Lifecycle

- Operation: `update_versioned_asset`
- Asset target: `toolset_codex_version`
- Changed files: `2`
- Changed teams: `5`

### Release Implications
- Target provider family: `github_release`.
- Managed capability surface: `ghdp-team-toolset`.
- If this target ships through a managed GHDP capability release, the corresponding packaged asset bundle and content-manifest payload must be published together.
- If the effective release tag/version changes, the active content index entry must be updated before downstream sync consumers will observe the new version.
<!-- GHDP:END ASSET_LIFECYCLE -->
