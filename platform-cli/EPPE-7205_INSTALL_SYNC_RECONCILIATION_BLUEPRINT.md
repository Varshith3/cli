# EPPE-7205 Install/Sync Reconciliation Blueprint

## Scope

This blueprint covers the platform-team install and sync reconciliation issues
observed during:

- `ghdp tools install --team platform --tool codex`
- `ghdp tools install --team platform`
- `ghdp sync check`
- `ghdp sync run --auto-approve`

The concrete failure chain is:

1. `codex-skills-aws` becomes current because the Codex post-install flow syncs it.
2. `tableau-athena-jars` remains pending.
3. `sync check` can present `tableau-athena-jars` as actionable from the shared
   Tableau drivers root.
4. `sync run` later fails with `E_SYNC_NOT_INSTALLED` because the capability is
   not actually bootstrap-installable from generic sync state.
5. Shared-root state can remain ambiguous when previously persisted detection
   data is treated as durable install evidence.

## Goals

1. Make sync capability detection correct for shared install roots.
2. Keep previously installed capabilities repairable even when tracked files are
   partially missing.
3. Make `sync run` output consistent with the actual bootstrap/repair/update
   actions the engine can apply.
4. Prevent misclassified shared-root capabilities from entering repair/apply
   paths and aborting the broader platform sync reconciliation path.
5. Make the persisted-state contract explicit so stale detected-only state does
   not resurrect false installs.

## Non-Goals

1. Do not redesign the release-content provider model.
2. Do not move install or sync logic across architecture layers.
3. Do not change the tool manifest structure or team membership in this work.
4. Do not introduce a new interactive command UX for sync in this ticket.
5. Do not reorder Codex or Claude post-install side effects in this ticket
   unless validation proves the current ordering causes a regression in the
   target repro flow.

## Architectural Constraints

1. Keep command modules thin and orchestration-focused.
2. Keep release-content state resolution in `core/release_content.py`.
3. Keep subprocess execution behind `exec/runner.py`.
4. Express install-vs-bootstrap policy through capability metadata and engine
   logic, not command-specific hardcoding.
5. Treat only durable release-content state sources such as `release` or
   `existing` as install evidence for repair flows; `detected` alone must not
   keep a shared-root capability in a repairable state by itself.

## Phase 1: Detection and State-Contract Hardening

### Changes

1. Update shared-root capability detection so unrelated local files do not make
   a capability appear installed.
2. Treat a capability as installed only when one of the following is true:
   - tracked files are present locally
   - GHDP has durable recorded install state for that capability and root
3. Preserve extra local file reporting so scan/check surfaces still show manual
   files in shared roots.
4. Define the persisted-state contract for shared roots so stale detected-only
   state does not count as install evidence for repairable capability state.
5. Keep stale detected-only state handling inside release-content state
   interpretation, or a backward-compatible state migration, and not in command
   handlers.

### Acceptance Criteria

1. A shared root with only unrelated files is reported as `not_installed`.
2. A previously installed capability with missing tracked files remains
   `partial` and repairable.
3. `tableau-athena-jars` no longer enters the repair path from unrelated files
   alone.
4. A previously persisted false-positive detected state does not keep a
   shared-root capability repairable when its tracked files were never
   installed.

## Phase 2: Sync Run Action Consistency

### Changes

1. Make `sync run` explicitly surface bootstrap installs separately from
   repairs.
2. Improve `sync run` summary output so blocked-only cases are reported clearly.
3. Preserve the current architecture split where the engine computes actions and
   the command renders them.
4. Keep action classification in the engine; `commands/sync.py` must only render
   `action` and `recovery_mode` and must not recompute bootstrap, repair,
   blocked, or update categories.
5. If blocked capabilities surface next-step recovery guidance, model that
   guidance as capability-owned metadata or engine output instead of hardcoded
   command-specific branches.

### Acceptance Criteria

1. Bootstrap-only runs report bootstrap actions before apply and after apply.
2. Blocked-only runs finish cleanly with a blocked summary instead of a false
   "no actions needed" message.
3. Update and repair behavior for already installed capabilities remains
   unchanged.
4. Genuine repair or update execution failures still fail `sync run`; only
   misclassified shared-root items stop causing the Tableau-style abort.
5. When a blocked capability has an owner bootstrap path, the CLI can surface a
   recommended next step without hardcoding that recommendation directly in the
   sync command layer.

## Phase 3: Install/Sync Handoff Validation and Regression Coverage

### Changes

1. Add engine-level tests for:
   - shared-root manual files without tracked install state
   - shared-root recorded installs with missing tracked files
   - mixed blocked/update reconciliation
2. Add CLI-level sync reporting tests for bootstrap and blocked summaries where
   the local test environment can support the command module import path.
3. Validate the real repro path across:
   - `ghdp tools install --team platform --tool codex` with adoption declined
   - `ghdp sync check`
   - `ghdp sync run --auto-approve`
4. Explicitly treat Codex/Claude post-step ordering as a preserved assumption
   for this ticket unless validation proves it must change.
5. Re-run targeted verification for release-content flows.
6. Do not add capability-specific or tool-specific exceptions in the sync
   engine for Codex, Claude, or Tableau; if post-step ordering ever changes,
   that change remains in `tools/service.py` as a separate tools-layer
   decision.
7. Update user-facing docs and command reference text for any blocked-vs-repair,
   bootstrap, or summary wording changes introduced by this work.

### Acceptance Criteria

1. Release-content regression tests cover the Tableau-style shared-root case.
2. Existing bootstrap/update repair tests still pass.
3. The changed modules compile cleanly.
4. The exact repro path above no longer aborts on `tableau-athena-jars`.
5. The blueprint outcome clearly states that Codex/Claude post-step ordering was
   either preserved by design or changed intentionally with test coverage.
6. README and command-reference wording match the implemented sync behavior.

## Risks

1. Shared-root detection could regress legitimate repair flows if recorded state
   is not preserved correctly.
2. CLI sync reporting could diverge from engine semantics if installs/repairs
   are summarized inconsistently.
3. Local CLI test execution may be limited by missing dev dependencies in the
   repo venv.
4. Stale detected-only state could mask the fix if the state contract is left
   implicit.
5. Touching Codex/Claude post-step ordering without a separate decision could
   reintroduce pending sync work that the current platform bootstrap flow
   relies on.

## QA Focus

1. Shared root with only manual files.
2. Shared root with recorded install plus missing tracked files.
3. Bootstrap-allowed capability with empty target root.
4. Mixed sync run containing one blocked capability and one update candidate.
5. Platform-team install followed by sync reconciliation.
6. Persisted stale detection state from a prior false-positive shared-root scan.
7. Blocked capability with recovery guidance available from capability metadata.

## Release Notes Draft

- Harden sync capability detection for shared install roots such as Tableau
  drivers.
- Make `ghdp sync run` report bootstrap, repair, update, and blocked states more
  clearly, including blocked-only summaries and capability-owned next-step
  guidance when available.
- Add regression coverage for shared-root sync reconciliation.
