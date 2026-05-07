## Agent Orchestration Journey

### Summary

This feature was delivered with an orchestrator-led flow in the main thread plus multiple sub-agents for discovery and design review.

### Agents Spawned

1. `Pasteur` (`explorer`, `gpt-5.4-mini`)
2. `Noether` (`explorer`, `gpt-5.4-mini`)
3. `Mencius` (`explorer`, `gpt-5.4-mini`)
4. `Laplace` (`explorer`, `gpt-5.4`)

### Sequence

1. The main orchestrator inspected repo structure, GHDP context, architecture rules, command patterns, and current branch state.
2. The main orchestrator ran live Windows Task Scheduler experiments to validate quoting, action-length, wrapper, and XML-query behavior.
3. Discovery agents were launched in parallel to map adjacent patterns in the codebase.
4. The orchestrator synthesized those findings into the scheduler design.
5. A design-review agent was asked to challenge the proposed shape for architecture and policy violations.
6. The orchestrator implemented the feature locally, ran targeted tests, updated docs, and used the design review feedback to tighten remaining validation and coverage.

### Prompts Used

`Pasteur`

Inspect this repo for existing sync/update/repair capability patterns and advise how a new scheduler capability should model tracked config/artifact files. Focus on relevant files under platform-cli/src and README. Return concise findings with file paths and recommend whether scheduler definitions should live in repo files, user state, or release-content artifacts.

`Noether`

Inspect this repo for command registration, config, state, and interactive prompt patterns that are most relevant to adding a new top-level `ghdp schedule` command group. Focus on Typer wiring, config keys, state/store usage, and non-interactive behavior. Return concise findings with file paths and implementation guidance.

`Mencius`

Inspect this repo for testing, packaging, release workflow, and manual release/pre-release flows. Focus on test locations/commands, pipx install path, GitHub workflow names, release notes, and any PR/release conventions. Return concise findings with file paths and actionable guidance for this feature branch.

`Laplace`

Review this proposed feature design against the repo's ARCHITECTURE.md and AGENTS instructions. Goal: identify design violations, architecture/policy conflicts, and missing considerations.

Proposed design:
- Add new command group `ghdp schedule` in `platform-cli/src/platform_cli/commands/schedule.py` with subcommands: `init`, `add`, `list`, `check`, `apply`, `repair`, `remove`, and hidden `run-job`.
- Keep commands thin; put logic in `platform-cli/src/platform_cli/tools/scheduler.py` and `platform-cli/src/platform_cli/tools/scheduler_windows.py`.
- Desired state is stored in Git-tracked repo manifest `.ghdp/schedule/schedules.json` for repo-owned schedules.
- User-added schedules are stored in `~/.ghdp/schedule/user-schedules.json`.
- Local runtime observations go to `~/.ghdp/state/state.json` via `update_tool_state`, keyed per scheduled job.
- Windows implementation only for now, but model/provider abstraction should be ready for launchd/cron later.
- Windows provider creates wrapper scripts under `~/.ghdp/schedule/wrappers/` and registers Scheduled Tasks that call the wrapper, because Task Scheduler action length is constrained.
- Jobs execute GHDP subcommands (`argv` form) through a hidden `ghdp schedule run-job` command, which writes per-job run logs under `~/.ghdp/schedule/logs/` and updates state.
- `schedule add` should be interactive when fields are missing, but fail cleanly in `--non-interactive` mode unless explicit flags are provided.
- `schedule apply`/`repair`/`remove` should require confirmation unless `--auto-approve` is passed.
- Seed repo manifest with one recommended mandatory job using existing command `ghdp sync run --auto-approve`; do not add non-existent log-delivery command yet.
- Update README, command reference TOML, release notes, and tests.

Please return:
1. concrete violations or risks with file references/rules
2. what to change before implementation
3. if acceptable, say so explicitly and mention any residual risks.

Follow-up sent to `Laplace`

Please return a concise final review now. I need: 1) any architecture/policy violations in the proposed scheduler design/implementation approach, 2) what to change before release, 3) if acceptable, say acceptable with residual risks only.

### What Each Agent Contributed

`Pasteur`

- identified the `sync` engine as the closest architectural analogue
- recommended Git-tracked repo manifests for repo-owned schedules
- recommended local state only for runtime observations

`Noether`

- mapped command registration, config, and state conventions
- confirmed `commands/schedule.py` plus `tools/` services fits the repo structure
- recommended `sync`-style confirmation and non-interactive error behavior

`Mencius`

- mapped pytest/build/pipx packaging flow
- identified the manual pre-release workflow to use
- surfaced release-notes freshness requirements before manual build

`Laplace`

- confirmed the design was acceptable within the repo architecture
- flagged the main risks: state must stay observational, manifest precedence must be explicit, validation must be enforced, and release notes must be updated before pre-release

### Main Orchestrator Work

The main orchestrator handled:

- repo and architecture inspection
- live Windows scheduler experiments
- feature design synthesis
- local implementation
- test execution
- documentation updates
- release flow preparation

### Key Decisions Taken Without User Round-Trips

- selected Git-tracked repo manifest plus user-local manifest as the cleanest split
- implemented Windows first with wrapper scripts because Task Scheduler command length is constrained
- seeded the repo manifest with `ghdp sync run --auto-approve` rather than inventing a non-existent log command
- kept launchd/cron as explicit future-provider work rather than adding shallow placeholder behavior

---

## EPPE-7391: Phase 1 Agentic Orchestrator Foundation

### Summary

This branch restarted the orchestration effort from a new frozen ground-truth document and treated that document as the single source of truth. The first delivery slice was Stage A: establish the repo-level orchestrator contract, create the live branch runtime skeleton, and add a native CLI inspection path so the contract is executable and testable instead of existing only as documentation.

### Frozen Inputs

- Ticket: `EPPE-7391`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Intent file refreshed for this branch:
  - `.ghdp/frbr/intent.json`
- Ground truth:
  - `phase1_agentic_orchestrator_ground_truth.md`

### Stage A Goals

1. Establish repo-level contract roots under `.ghdp/`:
   - `.ghdp/agents/`
   - `.ghdp/skills/`
   - `.ghdp/plugins/`
   - `.ghdp/memory/`
   - keep runtime state under `.ghdp/orchestrate/`
2. Define the baseline Phase 1 agent/skill/plugin inventory from the ground-truth document.
3. Create branch-scoped runtime artifacts for the active EPPE-7391 branch.
4. Add a native CLI inspection path so the contract can be validated immediately.
5. Keep the implementation aligned with the repo pattern:
   - `manifests/` owns loading and validation
   - `tools/` owns runtime-facing inspection and composition

### What Was Implemented In Stage A

#### Repo-level contracts

- `.ghdp/agents/manifest.json`
- `.ghdp/agents/AGENTS.md`
- `.ghdp/agents/<agent-id>.json`
- `.ghdp/skills/manifest.json`
- `.ghdp/skills/SKILLS.md`
- `.ghdp/plugins/manifest.json`
- `.ghdp/plugins/PLUGINS.md`
- `.ghdp/memory/manifest.json`
- `.ghdp/memory/README.md`
- `.ghdp/memory/shared/README.md`
- `.ghdp/memory/context/README.md`
- `.ghdp/orchestrate/README.md`

#### Branch runtime skeleton

- `.ghdp/orchestrate/branches/feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory/poa.md`
- `.ghdp/orchestrate/branches/feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory/branch_state.json`
- `.ghdp/orchestrate/branches/feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory/handoff.md`
- `.ghdp/orchestrate/branches/feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory/runs/20260504-223548-ist__codex__stage-a/run_state.json`
- `.ghdp/orchestrate/branches/feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory/runs/20260504-223548-ist__codex__stage-a/stage_status.json`
- `.ghdp/orchestrate/branches/feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory/runs/20260504-223548-ist__codex__stage-a/decisions.json`
- `.ghdp/orchestrate/branches/feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory/runs/20260504-223548-ist__codex__stage-a/resume_context.md`

#### Native CLI inspection path

- `platform-cli/src/platform_cli/commands/orchestrate.py`
- `platform-cli/src/platform_cli/tools/orchestrate_contract.py`
- `platform-cli/src/platform_cli/manifests/orchestrate_load.py`
- `platform-cli/src/platform_cli/manifests/orchestrate_validate.py`

#### CLI reference and tests

- `platform-cli/00_GHDP_CLI_COMMANDS_REFERENCE.toml`
- `platform-cli/tests/test_orchestrate_contract.py`
- `platform-cli/tests/test_orchestrate_manifests.py`

### Architectural Correction Made During Stage A

The first cut put contract loading and light validation directly in the tool layer. That was explicitly corrected before closing the checkpoint.

Final layering for Stage A:

- `manifests/orchestrate_load.py`
  - owns JSON loading
- `manifests/orchestrate_validate.py`
  - owns manifest-shape validation
- `tools/orchestrate_contract.py`
  - owns runtime-facing inspection/composition only

This matches the repo’s existing pattern instead of leaving manifest concerns embedded in the tool layer.

### Validated Runtime Status

The Stage A contract is now CLI-verifiable through:

- `ghdp orchestrate status`
- `ghdp --json orchestrate status`

### Asset Lifecycle Follow-up

After the Phase 1 flow was completed, one important modeling gap was identified: the orchestrator understood code-heavy SDLC work better than GHDP capability-asset work.

That gap was corrected by adding:

- repo-level asset lifecycle contracts:
  - `.ghdp/agents/asset-lifecycle.json`
  - `.ghdp/skills/asset-capability-discovery/SKILL.md`
  - `.ghdp/skills/asset-lifecycle-operations/SKILL.md`
  - `.ghdp/plugins/asset-lifecycle-sync/plugin.json`
- front-door routing support so asset-only requests can be recognized early
- a native command:
  - `ghdp orchestrate asset-lifecycle`

The first concrete executable asset target is:

- `toolset_codex_version`

which revises the Codex minimum-version requirement in:

- `platform-cli/src/platform_cli/resources/manifests/toolset.json`
- `platform-cli/release-assets/team_toolset/toolset.json`

This gives Phase 1 a lighter path for create/revise/version-update/remove asset work without forcing every asset request through the full SDLC by default.

Validated status at checkpoint:

- `agents_count = 19`
- `skills_count = 19`
- `plugins_count = 7`
- `memory_partition_count = 2`
- `repo_contract_ready = true`
- `branch_runtime_ready = true`
- `contract_ready = true`
- `active_run_key = 20260504-223548-ist__codex__stage-a`

### Tests Run

- `pytest platform-cli/tests/test_orchestrate_contract.py -q`
- `pytest platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_manifests.py -q`
- `ghdp --json orchestrate status --repo-root <repo>`
- `ghdp orchestrate status --repo-root <repo>`

### Checkpoint Commit

- Commit: `6da903c`
- Message: `Add phase1 orchestrator contract foundation`

This was committed and pushed before moving to the next implementation slice so the Stage A foundation is resumable and reviewable independently.

### What Comes Next

The next planned slice is Stage B: core orchestrator runtime.

That stage will add:

- run bootstrap logic
- policy loading
- branch/run state mutation helpers
- stage transition handling
- pause/resume behavior
- anomaly and decision recording helpers

### Why This Stage Matters

Stage A intentionally did not try to build the whole orchestrator at once.

Its purpose was to freeze:

- the repo-level contract shape
- the branch runtime shape
- the baseline inventory of Phase 1 agents/skills/plugins
- and the first executable inspection path

so later runtime work can grow on a stable contract instead of redesigning storage and ownership halfway through implementation.

### Stage B: Core Runtime Bootstrap

After the Stage A contract checkpoint, the next slice focused on making the branch runtime executable instead of only inspectable.

#### What Was Added

- packaged policy loading:
  - `platform-cli/src/platform_cli/manifests/orchestrate_policy_load.py`
  - `platform-cli/src/platform_cli/resources/policy/orchestrate_policy.json`
- runtime control-plane helpers:
  - `platform-cli/src/platform_cli/tools/orchestrate_runtime.py`
- native CLI lifecycle commands added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate start`
    - `ghdp orchestrate resume`
    - `ghdp orchestrate handoff`
- policy validation extended in:
  - `platform-cli/src/platform_cli/manifests/orchestrate_validate.py`
- contract/runtime path handling tightened in:
  - `platform-cli/src/platform_cli/tools/orchestrate_contract.py`

#### Runtime Behavior Now Supported

- start a branch-scoped orchestrator run
- reuse the existing active run for the current feature branch
- pause the active run with an explicit handoff
- resume the paused run
- load packaged policy defaults cleanly when no user-global override exists
- persist Stage B runtime state into the repo-local `.ghdp/orchestrate/...` artifacts

#### Key Corrections Made During Stage B

- runtime branch folder naming now compacts only when path length would be unsafe, while still preferring the existing full branch folder when it already exists
- timezone resolution now falls back cleanly when `Asia/Calcutta` is unavailable in the local environment
- resume context files were normalized so the current focus, next action, and activity log stay coherent across start/resume/handoff transitions

#### Live Branch Runtime State At Checkpoint

The active EPPE-7391 branch runtime now records:

- `current_stage = stage_b_runtime_bootstrap`
- `status = paused`
- `next_action = Continue into front-door intake, classification, and planning orchestration.`

This means the branch is intentionally paused at the end of Stage B with a clean handoff into Stage C.

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_manifests.py platform-cli/tests/test_orchestrate_runtime.py -q`
- source-driven CLI validation for:
  - `ghdp --json orchestrate start --repo-root <repo>`
  - `ghdp --json orchestrate handoff --summary ... --next-action ... --repo-root <repo>`
  - `ghdp --json orchestrate status --repo-root <repo>`

#### What Comes Next

Stage C will implement the front-door orchestration layer:

- intake sufficiency
- work-type classification
- autonomy assessment
- context/capability discovery
- parallel-work awareness
- POA planning

### Stage C: Front-Door Gates

Stage C turned the frozen front-door contract into executable runtime behavior instead of leaving intake and planning gates as documentation-only concepts.

#### What Was Added

- front-door orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_front_door.py`
- native CLI lifecycle command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate front-door`
- packaged policy defaults extended in:
  - `platform-cli/src/platform_cli/resources/policy/orchestrate_policy.json`
- policy validation extended in:
  - `platform-cli/src/platform_cli/manifests/orchestrate_validate.py`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_front_door.py`

#### Front-Door Behavior Now Supported

- intake sufficiency scoring
- work-type classification
- autonomy assessment
- context/capability discovery
- parallel-work awareness
- spec action decision
- POA refresh with Stage C outputs
- branch/runtime state transition from Stage B into Stage C

#### Live Branch Runtime State At Checkpoint

The active EPPE-7391 branch runtime now records:

- `current_stage = stage_c_front_door_gates`
- `status = paused`
- `next_action = Run Stage D architecture review and UX/DX review using the refreshed POA.`
- `work_type = new_feature`
- `autonomy_level = semi_autonomous`

Stage status was also normalized so:

- Stage A is marked completed
- Stage B is marked completed
- Stage C is marked completed

This makes the branch history read like a real orchestration progression instead of leaving old stages forever in progress.

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_manifests.py platform-cli/tests/test_orchestrate_runtime.py platform-cli/tests/test_orchestrate_front_door.py -q`
- source-driven CLI validation for:
  - `ghdp --json orchestrate front-door --repo-root <repo>`
  - `ghdp --json orchestrate status --repo-root <repo>`

#### What Comes Next

Stage D will implement the review layer:

- architecture review
- UX/DX review

### Stage D: Review Layer

Stage D converted the planned review personas into executable repo-backed review behavior rather than leaving architecture and UX/DX review as chat-only practices.

#### What Was Added

- review-layer orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_review.py`
- native CLI review command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate review`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_review.py`

#### Review Behavior Now Supported

- architecture review against the repo-level contract split and manifest/tool layering
- UX/DX review against the current orchestrate command surface and resume artifacts
- persistent review findings written into the branch run artifacts
- POA refresh with a Stage D review section
- branch/runtime state transition from Stage C into Stage D

#### Live Branch Runtime State At Checkpoint

The active EPPE-7391 branch runtime now records:

- `current_stage = stage_d_review_layer`
- `status = paused`
- `next_action = Proceed into Stage E implementation, regression planning, and test coverage authoring.`

The current accepted findings are non-blocking:

- architecture layer accepted
- UX/DX layer accepted
- residual risk remains around heuristic capability discovery and the still-verbose stage-by-stage command surface

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_manifests.py platform-cli/tests/test_orchestrate_runtime.py platform-cli/tests/test_orchestrate_front_door.py platform-cli/tests/test_orchestrate_review.py -q`
- source-driven CLI validation for:
  - `ghdp --json orchestrate review --repo-root <repo>`
  - `ghdp --json orchestrate status --repo-root <repo>`

#### What Comes Next

Stage E will begin the execution layer:

- implementation
- regression planning/protection
- new coverage authoring
- developer test execution
- later release-path expansion on top of those outputs

### Stage E: Execution Prep and Repo-Level Skill/Plugin Payloads

The first Stage E slice focused on making the execution layer concrete before actual code-mutation and release-driving loops begin.

#### What Was Added

- execution-prep orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_execution.py`
- native CLI execution-prep command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate execution-prep`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_execution.py`
- repo-level execution skill payloads added under:
  - `.ghdp/skills/<skill-id>/SKILL.md`
- repo-level execution plugin payloads added under:
  - `.ghdp/plugins/<plugin-id>/plugin.json`
- repo-level agent contracts refactored into:
  - one manifest index at `.ghdp/agents/manifest.json`
  - one explicit agent contract file per sub-agent at `.ghdp/agents/<agent-id>.json`

#### Execution Prep Behavior Now Supported

- load execution-layer sub-agent contracts directly from `.ghdp/agents/<agent-id>.json`
- derive allowed skills explicitly from those agent contracts instead of hardcoded runtime lists
- derive allowed plugins explicitly from those agent contracts instead of skill-only inference
- generate repo-backed execution artifacts:
  - `implementation_plan.md`
  - `qa_scenario_plan.md`
  - `regression_plan.md`
  - `coverage_plan.md`
  - `execution_bindings.json`

#### Live Branch Runtime State At Checkpoint

The active EPPE-7391 branch runtime now records:

- `current_stage = stage_e_execution_prep`
- `status = paused`
- `next_action = Start stage11 implementation using implementation_plan.md, then execute regression, coverage, and developer test plans.`

Pending downstream stages are now explicitly present in stage status:

- `stage11_implementation`
- `stage13_qa_scenario_design`
- `stage14_touched_scope_regression`
- `stage15_new_test_coverage`
- `stage16_developer_test_execution`

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_manifests.py platform-cli/tests/test_orchestrate_runtime.py platform-cli/tests/test_orchestrate_front_door.py platform-cli/tests/test_orchestrate_review.py platform-cli/tests/test_orchestrate_execution.py -q`
- source-driven CLI validation for:
  - `ghdp --json orchestrate execution-prep --repo-root <repo>`
  - `ghdp --json orchestrate status --repo-root <repo>`

#### What Comes Next

The next execution slice should move from planning artifacts into actual Stage 11 through Stage 16 runtime behavior:

- implementation loop
- regression execution loop
- new coverage authoring loop
- developer test execution loop

### Stage 11: Implementation Activation

This slice turns the repo-level `implementation` agent contract into a runnable branch packet instead of leaving Stage 11 as a pending placeholder.

#### What Was Added

- implementation-stage orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_implementation.py`
- native CLI implementation command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate implement`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_implementation.py`

#### Implementation Activation Behavior Now Supported

- load the repo-level `implementation` agent contract from:
  - `.ghdp/agents/implementation.json`
- validate the implementation agent's explicit `allowed_skills`
- validate the implementation agent's explicit `allowed_plugins`
- write repo-backed Stage 11 artifacts:
  - `implementation_prompt.md`
  - `implementation_bindings.json`
  - `implementation_summary.md`
- advance branch runtime into:
  - `current_stage = stage11_implementation`
  - `status = in_progress`

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_implementation.py platform-cli/tests/test_orchestrate_execution.py -q`

### Stage 12: Commit Push

This slice makes commit and push part of the orchestrated SDLC instead of leaving them as an external/manual gap.

#### What Was Added

- commit/push orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_commit_push.py`
- native CLI commit/push command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate commit-push`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_commit_push.py`

#### Commit Push Behavior Now Supported

- validate Stage 11 implementation is active
- stage the current repo changes
- generate a commit message and body from repo-backed runtime artifacts
- create the commit using the orchestrator-owned path
- push the active branch to the configured remote
- write repo-backed Stage 12 artifacts:
  - `commit_summary.md`
  - `commit_payload.json`
- advance branch runtime into:
  - `current_stage = stage12_commit_push`
  - `status = paused`

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_commit_push.py -q`

### Stage 13: QA Scenario Design

This slice turns the QA scenario designer into a real orchestrator-owned stage instead of leaving `qa_scenario_plan.md` as only a Stage E placeholder.

#### What Was Added

- QA scenario orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_qa.py`
- native CLI QA command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate qa-scenarios`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_qa.py`

#### QA Scenario Design Behavior Now Supported

- load the repo-level `qa-scenario-design` agent contract from:
  - `.ghdp/agents/qa-scenario-design.json`
- validate the QA agent's explicit `allowed_skills`
- validate the QA agent's explicit `allowed_plugins`
- derive acceptance anchors from `.ghdp/frbr/intent.json`
- generate repo-backed Stage 13 artifacts:
  - `qa_scenario_prompt.md`
  - `qa_scenario_bindings.json`
  - `qa_scenario_plan.md`
  - `qa_scenario_summary.md`
- advance branch runtime into:
  - `current_stage = stage13_qa_scenario_design`
  - `status = paused`

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_qa.py -q`

### Foundation Refactor: Repo-Owned Stage Recipes

Before continuing into Stage 14 and beyond, the orchestrator runtime was refactored so stage-owned prompt scaffolding, handoff wording, next-action guidance, and resume-note templates no longer live only inside the CLI engine.

#### What Was Added

- repo-level stage recipe index:
  - `.ghdp/orchestrate/stages/manifest.json`
- repo-level stage recipe docs:
  - `.ghdp/orchestrate/stages/STAGES.md`
- repo-level stage recipe contracts for the implemented stages:
  - `.ghdp/orchestrate/stages/stage_c_front_door_gates.json`
  - `.ghdp/orchestrate/stages/stage_d_review_layer.json`
  - `.ghdp/orchestrate/stages/stage_e_execution_prep.json`
  - `.ghdp/orchestrate/stages/stage11_implementation.json`
  - `.ghdp/orchestrate/stages/stage12_commit_push.json`
  - `.ghdp/orchestrate/stages/stage13_qa_scenario_design.json`
- manifest-layer stage loader:
  - `platform-cli/src/platform_cli/manifests/orchestrate_stage_load.py`
- manifest validation for stage contracts:
  - `platform-cli/src/platform_cli/manifests/orchestrate_validate.py`

#### What Changed In The Runtime

- `orchestrate status` now validates stage recipes as part of the repo-owned orchestrator contract
- front-door, review, execution-prep, implementation, commit-push, and QA scenario stages now read their human/agent-facing text from repo `.ghdp` stage contracts
- the CLI still owns execution, validation, state mutation, and safety checks

#### Why This Refactor Happened

The earlier slices proved the runtime behavior, but too much stage-owned guidance still lived in `platform-cli/src/platform_cli/tools/`.

This refactor intentionally pushed the declarative parts back into `.ghdp` so the split is cleaner:

- `.ghdp` owns the repo contract and stage recipes
- `platform-cli` owns the execution engine

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_manifests.py platform-cli/tests/test_orchestrate_runtime.py platform-cli/tests/test_orchestrate_front_door.py platform-cli/tests/test_orchestrate_review.py platform-cli/tests/test_orchestrate_execution.py platform-cli/tests/test_orchestrate_implementation.py platform-cli/tests/test_orchestrate_commit_push.py platform-cli/tests/test_orchestrate_qa.py -q`

### Stage 14: Touched Scope Regression

This slice turns touched-scope regression validation into a repo-backed orchestrator stage instead of leaving regression as only a Stage E planning note.

#### What Was Added

- regression-stage orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_regression.py`
- native CLI regression command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate regression`
- repo-level stage recipe added:
  - `.ghdp/orchestrate/stages/stage14_touched_scope_regression.json`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_regression.py`

#### Regression Behavior Now Supported

- load the repo-level `regression-validation` agent contract from:
  - `.ghdp/agents/regression-validation.json`
- validate the regression agent's explicit `allowed_skills`
- validate the regression agent's explicit `allowed_plugins`
- consume:
  - `regression_plan.md`
  - `qa_scenario_plan.md`
  - the current branch POA
- generate repo-backed Stage 14 artifacts:
  - `regression_prompt.md`
  - `regression_bindings.json`
  - `regression_selection.md`
  - `regression_summary.md`
- advance branch runtime into:
  - `current_stage = stage14_touched_scope_regression`
  - `status = paused`

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_regression.py -q`

### Stage 15: New Test Coverage

This slice turns new test coverage authoring into a repo-backed orchestrator stage instead of leaving coverage as only a Stage E planning note.

#### What Was Added

- coverage-stage orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_coverage.py`
- native CLI coverage command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate coverage`
- repo-level stage recipe added:
  - `.ghdp/orchestrate/stages/stage15_new_test_coverage.json`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_coverage.py`

#### Coverage Behavior Now Supported

- load the repo-level `test-coverage-authoring` agent contract from:
  - `.ghdp/agents/test-coverage-authoring.json`
- validate the coverage agent's explicit `allowed_skills`
- validate the coverage agent's explicit `allowed_plugins`
- consume:
  - `coverage_plan.md`
  - `qa_scenario_plan.md`
  - `regression_selection.md`
  - `implementation_plan.md`
- generate repo-backed Stage 15 artifacts:
  - `coverage_prompt.md`
  - `coverage_bindings.json`
  - `coverage_backlog.md`
  - `coverage_summary.md`
- advance branch runtime into:
  - `current_stage = stage15_new_test_coverage`
  - `status = paused`

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_coverage.py -q`

### Stage 16: Developer Test Execution

This slice turns developer test execution into a repo-backed orchestrator stage instead of leaving validation as only a manual follow-up after regression and coverage planning.

#### What Was Added

- test-execution orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_test_execution.py`
- native CLI test-execution command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate test-execution`
- repo-level stage recipe added:
  - `.ghdp/orchestrate/stages/stage16_developer_test_execution.json`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_test_execution.py`

#### Developer Test Execution Behavior Now Supported

- load the repo-level `developer-test-execution` agent contract from:
  - `.ghdp/agents/developer-test-execution.json`
- validate the execution agent's explicit `allowed_skills`
- validate the execution agent's explicit `allowed_plugins`
- consume:
  - `qa_scenario_plan.md`
  - `regression_selection.md`
  - `coverage_backlog.md`
- serialize local execution through a dedicated local lock before running tests
- execute the selected regression and authored coverage backlog with a deterministic pytest command
- generate repo-backed Stage 16 artifacts:
  - `test_execution_prompt.md`
  - `test_execution_bindings.json`
  - `test_execution_log.md`
  - `test_execution_summary.md`
- advance branch runtime into:
  - `current_stage = stage16_developer_test_execution`
  - `status = paused` on success
  - `status = blocked` on failure

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_test_execution.py -q`

### Stage 17: Packaged Artifact Validation

This slice turns packaged artifact validation into a repo-backed orchestrator stage instead of stopping the SDLC at source-mode and in-tree test execution.

#### What Was Added

- binary-validation orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_binary_validation.py`
- native CLI binary-validation command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate binary-validate`
- repo-level stage recipe added:
  - `.ghdp/orchestrate/stages/stage17_packaged_artifact_validation.json`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_binary_validation.py`

#### Packaged Artifact Validation Behavior Now Supported

- load the repo-level `binary-validation` agent contract from:
  - `.ghdp/agents/binary-validation.json`
- validate the binary-validation agent's explicit `allowed_skills`
- validate the binary-validation agent's explicit `allowed_plugins`
- resolve the package root for the CLI package under the repo
- serialize pipx install activity through a dedicated install lock
- install the CLI through pipx, then run a packaged CLI smoke path:
  - `ghdp --version`
  - `ghdp --json orchestrate status --repo-root <repo>`
- generate repo-backed Stage 17 artifacts:
  - `binary_validation_prompt.md`
  - `binary_validation_bindings.json`
  - `artifact_validation_result.md`
  - `artifact_validation_summary.md`
- advance branch runtime into:
  - `current_stage = stage17_packaged_artifact_validation`
  - `status = paused`

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_binary_validation.py -q`

### Stage 18: Release Readiness

This slice turns release readiness into an explicit repo-backed go/no-go decision instead of assuming that passing tests and a successful install automatically mean the branch is ready for prerelease creation.

#### What Was Added

- release-readiness orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_release_readiness.py`
- native CLI release-readiness command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate release-readiness`
- repo-level stage recipe added:
  - `.ghdp/orchestrate/stages/stage18_release_readiness.json`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_release_readiness.py`

#### Release Readiness Behavior Now Supported

- load the repo-level `release-readiness` agent contract from:
  - `.ghdp/agents/release-readiness.json`
- validate the release-readiness agent's explicit `allowed_skills`
- validate the release-readiness agent's explicit `allowed_plugins`
- review the accumulated evidence from:
  - `test_execution_summary.md`
  - `artifact_validation_summary.md`
  - `decisions.json`
  - `poa.md`
- generate repo-backed Stage 18 artifacts:
  - `release_readiness_prompt.md`
  - `release_readiness_bindings.json`
  - `release_readiness_summary.md`
- advance branch runtime into:
  - `current_stage = stage18_release_readiness`
  - `status = paused` when no blocking findings remain
  - `status = blocked` when prerelease progression should stop

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_release_readiness.py -q`

### Stage 19: Prerelease Creation

This slice turns prerelease creation into an orchestrator-owned stage that reuses the existing GHDP release engine instead of inventing a second prerelease path.

#### What Was Added

- prerelease orchestration engine:
  - `platform-cli/src/platform_cli/tools/orchestrate_prerelease.py`
- native CLI prerelease command added to:
  - `platform-cli/src/platform_cli/commands/orchestrate.py`
    - `ghdp orchestrate prerelease`
- repo-level stage recipe added:
  - `.ghdp/orchestrate/stages/stage19_prerelease_creation.json`
- focused coverage added in:
  - `platform-cli/tests/test_orchestrate_prerelease.py`

#### Prerelease Behavior Now Supported

- load the repo-level `release-prerelease` agent contract from:
  - `.ghdp/agents/release-prerelease.json`
- validate the prerelease agent's explicit `allowed_skills`
- validate the prerelease agent's explicit `allowed_plugins`
- reuse the existing release engine through:
  - `plan_binaries_release(...)`
  - `ensure_binaries_release(...)`
- generate repo-backed Stage 19 artifacts:
  - `prerelease_prompt.md`
  - `prerelease_plan.json`
  - `prerelease_summary.md`
- record either:
  - the prerelease tag and URL on success
  - or the concrete external blocker reason on failure

#### Live Outcome On EPPE-7391

The real branch run correctly blocked at this stage with:

- `E_RELEASE_NOTES_STALE:release_notes`

This is expected and healthy, because the stage is now honestly reflecting the existing release engine policy instead of pretending prerelease creation succeeded when branch release notes are stale.

#### Tests Run

- `pytest platform-cli/tests/test_orchestrate_prerelease.py -q`

### Stages 20-22: Release Notes Recovery, PR Integration, and Historian Closeout

The final Phase 1 slices moved the orchestrator beyond local validation and into real release/PR progression, then closed the branch run with durable traceability.

#### What Was Added

- reusable kernel and provider-adapter loading:
  - `platform-cli/src/platform_cli/manifests/orchestrate_kernel_load.py`
  - `platform-cli/src/platform_cli/orchestrate_kernel/provider_adapters.py`
  - `platform-cli/src/platform_cli/orchestrate_kernel/runtime_support.py`
  - `platform-cli/src/platform_cli/orchestrate_kernel/subagents.py`
- Stage 20 release-notes recovery:
  - `platform-cli/src/platform_cli/orchestrate_kernel/stage20_release_notes.py`
  - `.ghdp/orchestrate/stages/stage20_release_notes_refresh.json`
- Stage 21 PR and external integration:
  - `platform-cli/src/platform_cli/orchestrate_kernel/stage21_pr_external.py`
  - `.ghdp/orchestrate/stages/stage21_pr_external_integration.json`
- Stage 22 historian closeout:
  - `platform-cli/src/platform_cli/orchestrate_kernel/stage22_traceability.py`
  - `.ghdp/orchestrate/stages/stage22_traceability_capture.json`
- repo-level kernel/topology/scenario contracts:
  - `.ghdp/orchestrate/kernel.json`
  - `.ghdp/orchestrate/topology.json`
  - `.ghdp/orchestrate/scenarios/manifest.json`
  - `.ghdp/orchestrate/scenarios/new_feature_subagent_smoke.json`
- provider-host adapters:
  - `.ghdp/plugins/provider-vscode-codex/plugin.json`
  - `.ghdp/plugins/provider-vscode-claude/plugin.json`
  - `.ghdp/plugins/github-pr-gh/plugin.json`

#### What Happened Live On EPPE-7391

- Stage 20 refreshed branch release notes and committed the freshness fix:
  - commit: `9314c431eed3f92043076e605081840d5b57a341`
- Stage 19 was then rerun successfully and produced the prerelease:
  - `https://github.com/gh-org-data-platform/dp-tools-local-setup/releases/tag/v1.0.6-AgenticFrameworkFoundationSkillsPluginsSubagentsNativeMemory`
- Stage 21 reused the repo-owned PR/Jira path and completed with:
  - PR: `https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/64`
- Stage 22 finalized:
  - `historian_closeout.md`
  - final resume/handoff context
  - sub-agent scenario evidence in the active run folder

#### Reusable Kernel Direction Implemented

This branch now explicitly separates:

- `.ghdp`
  - repo-owned agents, skills, plugins, stage recipes, topology, and scenarios
- reusable kernel
  - provider resolution
  - topology-aware execution waves
  - stage 20/21/22 runtime execution
  - sub-agent packet building and persistence
- `platform-cli`
  - one host/entrypoint that consumes the kernel

#### Host Bootstrap Adapters Added

Repo-root host bootstrap files now exist so repository-hosted Codex and Claude sessions are explicitly pointed into `.ghdp` instead of relying only on implicit discovery:

- `AGENTS.md`
- `.codex/AGENTS.md`
- `.claude/AGENTS.md`

These files do not replace `.ghdp`. They act as host adapters that tell Codex/Claude to:

- treat `.ghdp` as the source of truth
- use repo-defined sub-agents, skills, and plugins
- respect `.ghdp/orchestrate/topology.json`
- route new work through the repo-defined work-type and stage flow

The implemented provider path now supports:

- `provider-codex`
- `provider-claude`
- `provider-vscode-codex`
- `provider-vscode-claude`

with repo-defined headless fallback behavior and repo-selected model compatibility for Codex CLI.

#### Provider-Hosted Sub-Agent Scenario Validation

The branch now includes a real repo-defined smoke scenario:

- `.ghdp/orchestrate/scenarios/new_feature_subagent_smoke.json`

It was executed through:

- `ghdp orchestrate subagent-scenario --scenario-id new_feature_subagent_smoke --execute-provider`

Validated outcome:

- requested host: `vscode_codex`
- effective host: `codex`
- effective provider plugin: `provider-codex`
- effective provider: `codex`
- fallback used: `true`
- execution waves persisted from `.ghdp/orchestrate/topology.json`
- prompt packets persisted in:
  - `subagent_execution_plan.json`
  - `subagent_prompt_packets.md`
  - `subagent_execution_result.json`

The live scenario proved that repo-defined sub-agents can now be packetized and executed through the reusable kernel/provider-adapter path instead of only existing as static contracts.

#### Residual Findings From The Live Scenario

The scenario succeeded, but the review sub-agents still surfaced a few real follow-up gaps:

- more prompt scaffolding could move from kernel code into repo-owned `.ghdp` contracts
- artifact paths and output schemas for prompt packets are still thinner than ideal
- the kernel is now topology-driven, but some future ordering and routing policy could still become even more declarative

Those are follow-up refinements, not blockers to the current Phase 1 foundation.

#### Checkpoint Commits For The Kernel/Provider Slice

- `e06f0d5` `Add reusable orchestrator kernel adapters and closeout stages`
- `5009fc9` `Honor repo-selected provider model compatibility`
- `5a7798b` `Honor repo topology for subagent execution waves`
- `5108c6e` `Tighten subagent provider contracts and packets`

### Phase 1 Hardening Slice

The next hardening pass closed the biggest remaining Phase 1 gaps that were still too implicit or too locally coupled.

#### What Was Added

- first-class asset lifecycle generalization:
  - `.ghdp/capability-allowlist.json`
  - `.ghdp/skills/asset-capability-discovery/SKILL.md`
  - `.ghdp/skills/asset-lifecycle-operations/SKILL.md`
  - `platform-cli/src/platform_cli/tools/orchestrate_asset_lifecycle.py`
- front-door phase regroup and restart policy:
  - `.ghdp/orchestrate/phases.json`
  - `platform-cli/src/platform_cli/tools/orchestrate_front_door.py`
- published prerelease retest:
  - `.ghdp/agents/published-prerelease-validation.json`
  - `.ghdp/skills/published-prerelease-retest/SKILL.md`
  - `.ghdp/orchestrate/stages/stage19b_published_prerelease_retest.json`
  - `platform-cli/src/platform_cli/orchestrate_kernel/stage19b_published_prerelease_retest.py`
- PR hygiene and prerelease commentary:
  - `.ghdp/skills/pr-branch-hygiene/SKILL.md`
  - `.ghdp/skills/pr-prerelease-commentary/SKILL.md`
  - `platform-cli/src/platform_cli/orchestrate_kernel/stage21_pr_external.py`
- configurable audit export backend:
  - `.ghdp/orchestrate/audit-export.json`
  - `.ghdp/skills/audit-export-persistence/SKILL.md`
  - `platform-cli/src/platform_cli/orchestrate_kernel/runtime_support.py`
  - `platform-cli/src/platform_cli/orchestrate_kernel/stage22_traceability.py`

#### What Changed In The Phase 1 Model

- asset-only work is no longer treated as a hidden side-effect of implementation
- release-backed and marketplace-backed capability families now share one repo-driven asset lifecycle path
- prerelease success is no longer “good enough” until the published artifact itself is validated
- PR progression now has an explicit branch-hygiene gate:
  - rebased on latest `origin/develop`
  - no merge commits after `origin/develop`
- closeout can now export the run packet through a configured backend:
  - local by default
  - AWS S3-ready once bucket/account details are provided later
- phase regroup/restart is now first-class in Stage C instead of being only informal operator judgment

#### Validation

- targeted hardening suite:
  - `pytest platform-cli/tests/test_orchestrate_front_door.py platform-cli/tests/test_orchestrate_asset_lifecycle.py platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_kernel_and_closeout.py -q`
- broad orchestrator suite:
  - `50 passed`

#### Phase 1 Status After This Slice

Phase 1 is now much closer to a freeze line.

The intentionally deferred items are still:

- repo-ready baseline files generated later through `ghdp reporting`
- AWS bucket/account-specific audit export wiring

Those are no longer architecture gaps inside the orchestrator itself. They are planned follow-on integration work.

### Merge Hygiene Finalization Gate

The next refinement made branch runtime retention explicit instead of leaving `.ghdp/orchestrate/branches/...` to drift into merge-bound history.

#### What Was Added

- repo-owned merge-hygiene policy:
  - `.ghdp/orchestrate/merge-hygiene.json`
- durable merge closeout skill:
  - `.ghdp/skills/merge-hygiene-finalization/SKILL.md`
- CLI-owned finalize and verify commands:
  - `platform-cli/src/platform_cli/tools/orchestrate_merge_hygiene.py`
  - `ghdp orchestrate finalize`
  - `ghdp orchestrate verify-merge-hygiene`
- thin GitHub Actions gate:
  - `.github/workflows/orchestrate-merge-hygiene.yml`

#### What Changed In The Runtime Model

- active feature work may keep branch-scoped runtime files under `.ghdp/orchestrate/branches/<branch>/...`
- before merge, `ghdp orchestrate finalize` now:
  - archives the runtime bundle locally for 7 days
  - promotes a durable closeout summary into `.ghdp/memory/shared/orchestrate-closeouts/`
  - prunes the runtime branch folder itself
- merge gating is now CLI-owned:
  - `ghdp orchestrate verify-merge-hygiene`
  - GitHub Actions only invokes the CLI check
- `ghdp orchestrate status` now understands two healthy feature-branch states:
  - active runtime present
  - finalized runtime pruned with merge-hygiene receipt present

#### Validation

- targeted tests:
  - `pytest platform-cli/tests/test_orchestrate_contract.py platform-cli/tests/test_orchestrate_merge_hygiene.py -q`
- broad orchestrator suite:
  - `53 passed`

This keeps runtime execution state temporary while preserving the branch’s durable memory and making the merge gate reusable both locally and in CI.
