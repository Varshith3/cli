# Phase 1 Prompt Gap Audit

Date: 2026-05-05
Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`

## Purpose

Compare the implemented Phase 1 agentic orchestrator against:

- the older development prompt used as a practical SDLC baseline
- the repo's actual `.ghdp` and orchestration contracts
- the need for lightweight capability-asset work in addition to full SDLC

## Newly Corrected Gap

The largest previously missing piece was capability-asset lifecycle handling.

That gap is now addressed through:

- `.ghdp/agents/asset-lifecycle.json`
- `.ghdp/skills/asset-capability-discovery/SKILL.md`
- `.ghdp/skills/asset-lifecycle-operations/SKILL.md`
- `.ghdp/plugins/asset-lifecycle-sync/plugin.json`
- front-door asset-only routing
- `ghdp orchestrate asset-lifecycle`

The first concrete live asset target implemented and validated is:

- `toolset_codex_version`

This successfully revised the Codex minimum version to `0.128.0` in:

- `platform-cli/src/platform_cli/resources/manifests/toolset.json`
- `platform-cli/release-assets/team_toolset/toolset.json`

## What Phase 1 Already Covers Well

- blueprint / POA generation
- architecture review
- UX / DX review
- implementation activation
- orchestrator-owned commit / push
- QA scenario generation
- regression selection
- test coverage authoring
- developer test execution
- packaged CLI validation with pipx lock discipline
- release readiness review
- prerelease creation
- PR / Jira progression
- historian closeout
- repo-owned agents / skills / plugins / stages / topology
- provider-adapter and sub-agent scenario groundwork

## Remaining Obvious Gaps

These are the clearest gaps still visible after comparing the old prompt and the current Phase 1 repo state.

### 1. Repo-ready baseline files are still missing in this repo

The host bootstrap files tell agents to consult:

- `.ghdp/readiness.json`
- `.ghdp/architecture.md`
- `.ghdp/runbook.yaml`
- `.ghdp/config.yaml`
- `.ghdp/guardrails.yaml`
- `.ghdp/lock.yaml`

But this repo currently does not contain those files.

Impact:

- Phase 1 orchestration works, but the repo-level operating contract is incomplete.
- Agents are pointed toward a richer readiness system than this repository currently provides.

Why this matters:

- for autonomous development, Stage 0 / repo baseline context should exist before deeper orchestration
- otherwise the repo tells hosts to look for governance files that are absent

### 2. Asset lifecycle is real, but not yet generic across all capability families

The new asset-lifecycle path is working, but the executable target set is still narrow.

Current concrete live target:

- `toolset_codex_version`

Not yet generalized end-to-end for all capability kinds, such as:

- GitHub release-backed capabilities with full content-manifest/content-index revision
- marketplace-repo capabilities
- generic create / revise / remove flows across the full capability inventory

Impact:

- the orchestration model now understands asset-only work
- but the operational surface is still an initial working slice, not the full asset lifecycle system

### 3. Post-prerelease install-from-release retest is not first-class yet

The old prompt explicitly expected:

- create prerelease
- use the published install command or artifact from that prerelease
- install it locally
- retest again from the published output

Current Phase 1 behavior validates packaged CLI install from local package root via pipx in Stage 17.

What is still missing:

- a first-class stage that installs from the actual published prerelease artifact or published install path after Stage 19/20 and reruns validation

Impact:

- Phase 1 proves packaged local install
- but does not yet fully prove "what users will actually install from the release page"

### 4. PR comment with prerelease link is not explicit yet

The older prompt expected:

- create / reuse PR
- add a PR comment with the prerelease link
- add Jira comment with prerelease link

Current Stage 21 behavior clearly handles:

- PR creation / reuse
- Jira update

But a dedicated PR comment step tied to the prerelease link is not clearly first-class in the runtime.

### 5. External tmp export of orchestration prompts / iterations is not first-class

The older prompt explicitly asked for:

- exact prompts
- sub-agent sequence
- iterations
- orchestration plan
- stored under a temp path

Phase 1 now stores a strong amount of evidence inside `.ghdp/orchestrate/...`, including prompt packets and run artifacts.

What is still not first-class:

- a standardized export stage that mirrors the required audit packet into a dedicated temp folder format automatically for every run

### 6. Multi-phase regroup / restart logic is not yet a formal orchestrator policy

The old prompt strongly emphasized:

- if work is too large, split it into phases
- if a phase grows too large, restart from blueprint / design / UX review / implementation / QA loop again

Phase 1 already supports staged execution well, but does not yet make:

- phase slicing
- re-entry from "too large, go back to blueprint"

an explicit first-class policy/state machine concept.

### 7. Stage 0 repo-readiness gating is not yet explicit inside the orchestrator flow

Related to the missing repo-ready files, the orchestration flow currently starts strongly from intake/planning/runtime.

What is not yet explicit:

- a formal Stage 0 or preflight gate that checks repo-readiness artifacts and blocks or scaffolds them before the rest of the SDLC starts

## Practical Priority Order

If these remaining gaps are addressed, the clean order should be:

1. repo-ready baseline and Stage 0 gating
2. generic capability asset lifecycle expansion
3. post-prerelease published-artifact retest
4. explicit PR prerelease comment step
5. formal tmp audit export
6. formal multi-phase regroup / restart policy

## Bottom Line

Phase 1 is already strong enough to be useful and real.

But the most important remaining gaps are not random polish items:

- the repo-ready baseline is incomplete
- asset lifecycle needs broader operational coverage
- published-prerelease retest is not yet first-class

Those are the highest-signal follow-ups before calling the autonomous development story truly rounded.
