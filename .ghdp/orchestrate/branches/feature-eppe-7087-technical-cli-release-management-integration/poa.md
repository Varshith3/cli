# EPPE-7087 POA: CLI-Owned Manual Build Binaries Release Flow

## Goal

Move the logic currently embedded in `.github/workflows/manual-build-binaries.yml` into GHDP CLI so the same release-build capability can run from:

- local laptop
- GitHub Actions
- Jenkins
- EC2 / Fargate
- any future executor

The workflow or CI runner should become an invocation layer only. GHDP CLI should own the actual release behavior.

## Intent Reference

- Ticket: `EPPE-7087`
- Branch: `feature/EPPE-7087-TECHNICAL-cli-release-management-integration`
- Repo intent: `.ghdp/frbr/intent.json`

## Current State

Today, `.github/workflows/manual-build-binaries.yml` owns most of the release behavior, including:

- source-ref and release-channel interpretation
- stable vs prerelease determination
- tag derivation
- feature-branch release-notes freshness validation
- release-note composition
- release create/edit behavior
- build metadata injection
- runtime-default injection
- PyInstaller build orchestration
- output asset preparation and checksum generation
- asset upload to the GitHub release

The CLI today has related helper pieces but does not own this release flow as a single capability.

## Target State

Introduce a GHDP release capability that can be invoked uniformly from any environment.

Proposed user-facing shape:

```bash
ghdp release plan-binaries \
  --source-ref <branch|tag|sha> \
  --workdir platform-cli \
  --release-visibility <auto|draft|published> \
  --release-channel <auto|prerelease|ga> \
  --python-version <version> \
  --release-notes-mode manual

ghdp release build-binaries \
  --source-ref <branch|tag|sha> \
  --workdir platform-cli \
  --release-visibility <auto|draft|published> \
  --release-channel <auto|prerelease|ga> \
  --python-version <version> \
  --release-notes-mode manual
```

The command family should:

1. validate inputs and runtime prerequisites
2. resolve release plan from repo state and inputs
3. validate release-notes expectations
4. compute tag / stable / prerelease / draft behavior
5. generate structured release notes
6. ensure the release exists or is updated
7. inject build metadata and runtime defaults
8. install dependencies and build the binary
9. prepare assets and checksums
10. upload assets to the release

## Design Direction

### Layering

- `commands/`: thin release CLI command surface only
- `tools/`: release planning and execution logic
- `core/`: only if common runtime helpers are needed across multiple features
- `exec/`: all subprocesses continue through `run_cmd`

### Capability-First Interpretation

This should be implemented as a reusable release capability, not as workflow-specific logic hidden behind GitHub Actions only.

### CI-Agnostic Principle

Runner-specific systems should only:

- checkout the repo
- provide auth / env / secrets
- install GHDP and prerequisites
- invoke the GHDP release capability

## Proposed File / Module Changes

### New command surface

- `platform-cli/src/platform_cli/commands/release.py`

Responsibilities:

- expose `ghdp release ...` Typer commands
- parse CLI options
- call release planning / execution services
- print concise stage progress and outcomes

Recommended subcommands:

- `ghdp release plan-binaries`
- `ghdp release build-binaries`

Why:

- planning and execution are easier to test separately
- CI runners can optionally consume a CLI-generated plan first
- design review is easier because plan logic is isolated from side effects

### New release tools

- `platform-cli/src/platform_cli/tools/release/__init__.py`
- `platform-cli/src/platform_cli/tools/release/models.py`
- `platform-cli/src/platform_cli/tools/release/planner.py`
- `platform-cli/src/platform_cli/tools/release/executor.py`
- `platform-cli/src/platform_cli/tools/release/environment.py`
- `platform-cli/src/platform_cli/tools/release/metadata.py`

Suggested responsibilities:

- `planner.py`
  - release input normalization
  - source-ref classification
  - latest stable lookup
  - prerelease/stable tag computation
  - channel + draft/prerelease resolution
  - release-notes freshness rules

- `metadata.py`
  - release note assembly
  - summary-file loading / validation
  - build metadata file content
  - runtime-default metadata file content
  - asset naming and checksum metadata

- `environment.py`
  - environment detection and guardrails
  - runtime prerequisite checks
  - auth / executable validation
  - executor-specific context helpers that do not encode business logic

- `executor.py`
  - ensure release exists / update release
  - build invocation orchestration
  - asset preparation
  - release upload orchestration

- `models.py`
  - typed release-plan and execution-result objects
  - keep command/tool boundaries explicit and testable

### Existing modules likely to reuse

- `platform-cli/src/platform_cli/tools/release_notes.py`
- `platform-cli/src/platform_cli/tools/git_repo.py`
- `platform-cli/src/platform_cli/tools/ci_environment.py`
- `platform-cli/src/platform_cli/core/release_content.py`

### New bundled policy data

- `platform-cli/src/platform_cli/resources/release/manual_build_binaries_policy.json`

This should hold data-oriented policy such as:

- stable branch names
- branch-kind tokens to strip from feature-branch slug derivation
- release notes source path
- release notes template path
- supported asset matrix
- build metadata target file path
- runtime defaults target file path

This keeps release policy manifest-driven instead of burying it in Python.

### Workflow changes

Refactor `.github/workflows/manual-build-binaries.yml` to:

- keep checkout / runner setup / environment materialization
- invoke `ghdp release build-binaries ...`
- remove business logic once the CLI implementation is proven

## Migration Strategy

### Iteration 1

Move release planning logic into CLI:

- tag derivation
- stable/prerelease resolution
- notes freshness validation
- release-note composition

Workflow still performs build/upload using CLI-produced outputs only if needed.

### Iteration 2

Move build/upload execution into CLI:

- metadata injection
- dependency install
- PyInstaller build
- asset preparation
- checksum generation
- release upload

### Iteration 3

Reduce workflow to wrapper:

- setup
- install GHDP
- invoke CLI

## Configuration / Artifact Assessment

Potential release behavior that may fit data-backed or artifact-backed configuration:

- release tag naming policy
- branch-kind parsing aliases
- release-note file locations
- supported asset matrix
- runtime-default injection mapping

Potential behavior that should remain code:

- orchestration sequencing
- subprocess execution
- release existence/update flow
- upload behavior
- build failure handling

Reevaluation result:

- For Phase 1, release policy should become a bundled resource manifest inside the CLI package, not a GitHub release-backed sync artifact.
- Reason: this release capability is core product behavior and should version together with the CLI code, tests, and command semantics.
- A sync-style GitHub release artifact may become useful later only if release policy must evolve independently of CLI releases or be shared broadly across repos.

## Critique Pass: Design / Architecture Findings

### No major design blockers

The overall direction is aligned with the CLI architecture and the repo intent.

### Changes required to avoid design violations

1. Do not encode the whole flow in `commands/release.py`.
   - command layer must stay thin
   - all planning/build/upload behavior belongs in `tools/release/*`

2. Do not create GitHub Actions-specific logic as the source of truth.
   - GH-specific env/output handling may exist only as adapter behavior
   - business logic must remain executor-agnostic

3. Do not hardcode release policy in multiple Python files.
   - asset matrix and release-rule constants should be data-backed in `resources/`

4. Do not move release-specific concerns into `core/` unless shared beyond this capability.
   - keep capability-local logic under `tools/release/*`

5. Do not bypass `exec/runner.py`.
   - all `gh`, `git`, `python`, and build commands must continue through `run_cmd`

### Revised implementation stance after critique

- Use a `ghdp release` command family with separate planning and execution commands
- Use a dedicated `tools/release/` package for capability logic
- Use a bundled release policy manifest under `resources/`
- Keep the workflow as a wrapper that eventually invokes the CLI capability
- Keep GitHub-specific behavior as adapter context, not business logic

## Acceptance Criteria Mapping

This work is successful when:

- GHDP can run the manual build-binaries release flow itself
- GitHub Actions no longer needs to own the core release logic
- the same GHDP release capability can be executed from local or CI contexts
- observability and traceability are preserved

## Risks

- putting too much environment-specific logic into the command layer
- overfitting behavior to GitHub Actions instead of keeping it runner-agnostic
- duplicating existing git/release helper logic instead of reusing it
- moving too much at once without keeping the workflow functional during migration

## Initial Recommendation

Implement a new `ghdp release` command family now, but migrate the workflow in controlled steps so:

- design stays capability-first
- architecture stays layered
- release behavior becomes portable
- the workflow remains usable during the transition

## Iteration 2 Blueprint: Remove Remaining Prepare-Job Workflow Logic

### Problem Statement

The current workflow is much thinner than before, but the `prepare-release` job still contains GitHub Actions-specific logic that:

- invokes `ghdp --json release plan-binaries`
- writes plan JSON to a temporary file
- runs inline Python to parse the JSON
- exports `tag`, `script_ref`, `is_main`, `is_stable`, `draft`, and `prerelease` into `GITHUB_OUTPUT`

This means the workflow is still doing adapter logic that GHDP CLI can own directly.

### Proposed Change

Keep the workflow responsible only for:

- checkout
- Python setup
- GHDP installation
- invoking `ghdp release prepare-binaries-release`

Move the remaining prepare-step output export into GHDP CLI by teaching the CLI command to detect and write GitHub Actions outputs itself.

### Recommended Shape

Extend:

- `ghdp release prepare-binaries-release`

with a GitHub Actions adapter behavior that:

- detects `GITHUB_OUTPUT` from the environment
- appends the workflow output fields directly from the computed plan
- continues to ensure the release exists

So the workflow can reduce to a single CLI prepare step with a step id, while GHDP handles both:

1. prepare-stage release behavior
2. GitHub-output export for downstream jobs

### Suggested Implementation Details

- keep command parsing in `commands/release.py`
- add adapter logic in `tools/release/` rather than embedding file-format logic in the workflow
- use `GITHUB_OUTPUT` as an env-driven adapter, not as a required CLI-only concept
- preserve `plan-binaries` for local/manual inspection and debugging

### Acceptance Criteria For This Iteration

- the workflow no longer writes or parses `release_plan.json`
- the workflow no longer uses inline Python to shape prepare-step outputs
- `prepare-binaries-release` alone is enough for the prepare job
- local/manual CLI use remains unchanged
- GitHub Actions run still completes successfully for the branch

## Critique Persona: Architecture Review

### Findings

1. The current workflow still leaks executor-specific orchestration logic.
   - This is not a major violation, but it is below the intended end-state.
   - The CLI should own GitHub-output export when that export is derived from CLI-owned release planning.

2. GitHub-specific adapter logic is acceptable only if it remains adapter behavior.
   - It should not become the source of truth for release semantics.
   - Output-field writing should be driven from the already-computed release plan.

3. The command layer must stay thin.
   - `commands/release.py` should not manually assemble output-file contents.
   - Use a small helper under `tools/release/` for GitHub-output export.

4. This is not a GitHub release-content artifact candidate.
   - `GITHUB_OUTPUT` field export is executor adapter behavior, not policy/configuration.
   - It should not be modeled like a sync capability or release-backed content manifest.

### Critique Conclusion

This enhancement should proceed, provided:

- GitHub-output writing is isolated as adapter logic
- release behavior itself remains in planner/executor functions
- workflow YAML loses the inline parse/export logic

No design blocker remains after those constraints.

## GitHub Artifact Reassessment

Rechecked against the sync/release-content pattern used elsewhere in GHDP:

- release policy and asset matrix remain best as bundled CLI resources
- GitHub-output export mapping is not a sync artifact candidate
- no new GitHub release-backed content file is justified for this iteration

Conclusion:

- do not convert this enhancement into a GitHub artifact-backed capability file
- keep it in CLI code because it is execution-adapter behavior tightly coupled to the command contract

## Iteration 3 Blueprint: Auto-Detect Release Workdir From Repo Structure

### Problem Statement

The current `ghdp release` commands still expose `--workdir` as if callers are expected to know and pass the CLI package folder every time.

For this repository, that is unnecessary friction because:

- the release capability currently targets the GHDP CLI only
- the repository follows a stable structure with `platform-cli/` as the package root
- both local and CI callers are repeatedly passing the same value

This makes `--workdir` feel like an implementation leak instead of a true input.

### Proposed Change

Keep `--workdir` as an advanced override, but make the default behavior auto-detect the release workdir from repo structure.

Recommended detection order:

1. explicit `--workdir`
2. policy default if it resolves cleanly from repo root
3. strict repo-structure auto-detection for a single CLI package folder
4. fail with a clear actionable error if no safe target can be determined

### UX Goal

The common path should work from repo root with:

```bash
ghdp release plan-binaries --source-ref <branch>
ghdp release prepare-binaries-release --source-ref <branch>
ghdp release build-binaries --source-ref <branch>
```

while still allowing:

```bash
ghdp release build-binaries --source-ref <branch> --workdir platform-cli
```

for explicit override or future edge cases.

### Suggested Implementation Shape

- keep CLI option parsing unchanged enough to remain backward compatible
- move detection logic into `tools/release/planner.py`
- add a dedicated resolver helper for release workdir selection
- keep bundled policy data as a default hint, not as the only path
- make failure non-interactive-safe with a precise error message

### Detection Rules

Safe detection should prefer repository conventions over broad fuzzy search.

Suggested rules:

- if `--workdir` is provided, use it exactly
- else if policy default exists and contains both `ghdp.spec` and `pyproject.toml`, use it
- else scan repo root for exactly one child directory containing both `ghdp.spec` and `pyproject.toml`
- if more than one candidate exists, fail and ask for explicit `--workdir`
- if no candidate exists, fail and explain what structure is expected

### Acceptance Criteria For This Iteration

- release commands work from repo root without `--workdir` in this repo
- existing explicit `--workdir platform-cli` calls continue to work
- workflow can eventually stop passing `workdir` once compatibility confidence is high
- planner emits stable errors for ambiguous or missing package roots
- command layer remains thin

## Critique Persona: Architecture Review For Auto-Detection

### Findings

1. Auto-detection is aligned with CLI UX and the repo's structure contract.
   - It removes repeated caller knowledge that should belong to the capability.

2. Detection must remain strict and deterministic.
   - A broad recursive search would be a design smell and could make CI behavior surprising.

3. `--workdir` should remain available as an escape hatch.
   - Removing it now would be risky because workflows and local callers may still rely on it.

4. The detection logic belongs in the planner, not the command layer.
   - The planner already resolves repo-scoped release inputs and is the correct place for capability-local path resolution.

5. This is not a GitHub artifact candidate.
   - Repo-structure detection is execution logic derived from local filesystem state, not synced policy content.

### Critique Conclusion

This enhancement should proceed, provided:

- detection stays strict and local to `tools/release/*`
- `--workdir` remains a supported override
- failure modes are explicit instead of silently guessing among multiple candidates

No architecture blocker remains after those constraints.

## GitHub Artifact Reassessment For This Iteration

Rechecked against the sync-style artifact pattern:

- repo-structure auto-detection is not a release-backed artifact concern
- the existing bundled policy resource remains the right place for the default workdir hint
- no additional GitHub artifact file is justified

Conclusion:

- keep this enhancement in CLI planner logic plus existing bundled policy
- do not introduce a sync artifact or release-content file for workdir detection
