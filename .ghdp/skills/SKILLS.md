# Phase 1 Skill Inventory

This file is the human-readable companion to `.ghdp/skills/manifest.json`.

Phase 1 skills are the reusable reasoning and execution contracts the orchestrator depends on. Each skill is intentionally worded so Codex, Claude, and human reviewers can follow the same intent without relying on hidden prompt memory.

Execution-ready skill payloads now begin to live under:
- `.ghdp/skills/<skill-id>/SKILL.md`

The first concrete payload set was added for the Stage E execution layer so prompts/contracts for testing, release, Jira, and traceability no longer depend only on manifest text.

## Skills

### `ticket-intake-sufficiency`
Resolve what is known, what is missing, and what clarification is needed before planning.

### `work-type-classification`
Choose the correct lifecycle path among feature, enhancement, bug fix, and maintenance.

### `complexity-autonomy-assessment`
Score ambiguity, technical complexity, blast radius, and approval posture.

### `capability-discovery`
Find the best reuse path and surface likely touched areas before implementation expands.

### `blueprint-poa-authoring`
Produce the canonical plan of action and keep it synchronized with design decisions.

### `architecture-compliance`
Check architecture alignment, capability-first compliance, coupling, and precedent quality.

### `ux-dx-review`
Check user/operator/contributor ergonomics and whether the experience remains practical.

### `qa-scenario-generation`
Produce tricky, realistic validation scenarios rather than shallow smoke-only checks.

### `touched-scope-regression`
Select and run the existing tests that protect already-working behavior.

### `test-coverage-authoring`
Design and write tests that encode newly introduced or changed behavior.

### `developer-test-execution`
Run the planned validation flow and drive the iteration loop when failures occur.

### `isolated-binary-validation`
Install and validate the packaged artifact in a controlled way.

### `release-and-pr`
Coordinate prerelease generation, PR progression, and release-facing steps.

### `published-prerelease-retest`
Validate the actual published prerelease artifact instead of stopping at local package-root validation.

### `pr-branch-hygiene`
Enforce rebasing onto the latest `develop` and block merge-commit history before PR creation.

### `pr-prerelease-commentary`
Attach the latest prerelease link to the PR through the approved GitHub path.

### `jira-acli-integration`
Use ACLI for Jira communication so the integration path stays explicit and testable.

### `minimal-sync-decision`
Decide whether the request remains repo-native or should be treated as sync-managed work.

### `traceability-and-resume`
Persist enough evidence and handoff structure that another person or agent can continue safely.

### `audit-export-persistence`
Mirror final run evidence to the configured local or AWS-backed export destination.

### `folder-backed-shared-memory`
Provide practical shared-memory behavior today without blocking on external memory systems.

### `repo-local-code-context`
Provide practical code-context summaries today without blocking on external graph tooling.

### `stable-release-notes-assembly`
Build stable release-note lineage separately from branch prerelease notes.

### `asset-capability-discovery`
Understand existing sync/capability assets, their provider family, and what must change before choosing the right lifecycle path.

### `asset-lifecycle-operations`
Create, revise, version-update, or retire repo-managed capability assets without forcing full SDLC when the request is asset-only.

### `phase-regroup-and-restart`
Make phase slicing and restart recommendations explicit when one pass is no longer the right unit of delivery.

### `merge-hygiene-finalization`
Archive runtime-only branch artifacts, promote the durable closeout summary into shared memory, and leave a receipt that local runs and CI can verify before merge.
