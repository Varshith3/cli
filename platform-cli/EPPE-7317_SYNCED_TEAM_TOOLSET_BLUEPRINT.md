# EPPE-7317 Blueprint: Onboard approved team list and move toolset mapping to GHDP-synced artifact config

## Objective

Make GHDP treat the synced, artifact-backed local `.ghdp` configuration as the source of truth for team-to-toolset onboarding. This phase uses the approved Jira team names verbatim:

- `data-engg`
- `platform-engg`
- `data-scientist`
- `data-analyst`

The installed toolset stays identical for every team in this phase. The work is about moving the mapping source and refresh behavior, not introducing team-specific variance.

## Architecture guardrails

This blueprint follows the repo's GHDP guidance:

- keep `commands/` thin controllers
- keep manifest loading and validation in `manifests/`
- keep cross-cutting runtime concerns in `core/`
- keep subprocess execution behind `exec/runner.py`
- let `PlatformError` bubble to the CLI formatter
- do not reorganize layers or folders
- prefer data-backed manifests over hardcoded logic
- treat `load_manifests()` as source selection only; freshness, `needs-sync`, and preflight semantics stay outside the loader
- if selective preflight is added later, keep it as narrow core-helper work triggered from thin `commands/tools.py` entrypoints

The main seam is to preserve the existing toolset schema shape while changing where runtime reads it from.

## What changes, at a glance

- `ghdp sync` gains a managed artifact flow for the team-toolset payload.
- runtime prefers the synced local GHDP config over the packaged `toolset.json` when present.
- missing or stale synced config becomes explicit, actionable behavior.
- team and tools commands continue to work against the resolved toolset without changing the per-team installed tool list.

## Phase 1: Blueprinted sync source and manifest loading

### Goal

Introduce the synced team-toolset artifact path and make it the runtime source of truth when present.

### File seams

- `platform-cli/src/platform_cli/manifests/load.py`
- `platform-cli/src/platform_cli/commands/sync.py`
- `platform-cli/src/platform_cli/core/release_content.py`
- `platform-cli/src/platform_cli/resources/manifests/toolset.json`

### Implementation intent

- Add a GHDP-managed local path under `.ghdp/policies/team-toolset.managed.json` for the synced team-toolset manifest so it does not collide with the existing user override path used by `load_manifests()`.
- Keep the schema identical to the current `toolset.json` shape so `load_manifests()` can read it unchanged.
- Define the sync asset/package layout explicitly:
  - capability id: the GHDP sync capability that owns the team-toolset payload
  - release manifest asset: the release asset that contains the managed `toolset.json`
  - managed local target path: `~/.ghdp/policies/team-toolset.managed.json`
  - user override path: `~/.ghdp/manifests/toolset.json` or `~/.ghdp/toolset.json`
  - packaged fallback: `platform_cli/resources/manifests/toolset.json`
- Make `load_manifests()` prefer the centrally synced managed copy when present, while preserving a clear fallback story for bootstrap/dev use until scheduled/background sync exists and keeping user overrides distinct.
- Reuse the existing sync/release-content architecture rather than inventing a one-off loader.

### Acceptance criteria

- The approved team list is available from the synced local GHDP config.
- Runtime reads the synced copy first when it exists.
- The packaged manifest remains a fallback/bootstrap source, not the long-term source of truth.
- No team-specific tool variance is introduced.

### Safety / rollback notes

- Keep the packaged `toolset.json` intact until the synced path is stable.
- Keep the packaged `toolset.json` bootstrap/dev-only until scheduled/background sync exists.
- If the synced file is missing, do not silently invent teams or tool mappings.
- Fail with a clear GHDP-facing message or fall back only in explicitly defined bootstrap paths.

### Fallback policy matrix

| Command / family | Managed config present and current | Managed config missing | Managed config stale |
| --- | --- | --- | --- |
| `team list` / `team current` | Use managed toolset | Fall back to packaged `toolset.json` only if bootstrap mode is allowed; otherwise `PlatformError(code=E_MANIFEST_NOT_FOUND, reason=toolset.json)` | Use managed toolset and surface refresh warning; if selected team is invalid after refresh, raise `PlatformError(code=E_TEAM_INVALID_AFTER_SYNC, reason=team.selected)` |
| `team use` | Validate against managed toolset | Fall back to packaged `toolset.json` only for first-team selection; otherwise deny with `PlatformError(code=E_TEAM_SELECTION_LOCKED, reason=team.switch)` | Revalidate selected team against refreshed managed list; deny invalid switches with `PlatformError(code=E_TEAM_INVALID_AFTER_SYNC, reason=team.selected)` |
| `tools validate` / `tools list` / `tools status` | Resolve against managed toolset | Fall back to packaged `toolset.json` and clearly label the source; if neither exists, fail with `E_MANIFEST_NOT_FOUND` | Use managed toolset and emit a stale-data warning that points to `ghdp sync` |
| `tools install` / `tools uninstall` | Resolve against managed toolset | Fall back only if the packaged manifest is available and the command can run safely; otherwise fail with `E_MANIFEST_NOT_FOUND` | Resolve against managed toolset and block if the selected team can no longer be resolved after refresh |

The stable error-code expectation is that missing, invalid, or stale sync state should always surface a deterministic `PlatformError` rather than a silent partial fallback.

## Phase 2: Team selection, tools resolution, and UX/DX behavior

### Goal

Make the existing team and tools flows consume the synced source cleanly and behave predictably when the managed config is missing, stale, or replaced.

### File seams

- `platform-cli/src/platform_cli/commands/team.py`
- `platform-cli/src/platform_cli/commands/tools.py`
- `platform-cli/src/platform_cli/commands/_access_common.py`
- `platform-cli/src/platform_cli/core/team_context.py`
- `platform-cli/src/platform_cli/core/access.py`
- `platform-cli/src/platform_cli/core/config.py`
- `platform-cli/src/platform_cli/state/store.py`
- `platform-cli/src/platform_cli/manifests/validate.py`
- `platform-cli/src/platform_cli/tools/service.py`

### Implementation intent

- Keep `team list`, `team current`, `team use`, `tools validate`, `tools list`, `tools status`, `tools install`, and related flows pointed at the resolved synced toolset.
- Treat the approved team list as canonical in this rollout.
- Preserve the same installed tools for every approved team.
- Keep `team.selected` persistence in `core/config.py` only.
- Persist managed sync provenance, install metadata, content hash, `last_verified_at`, stale markers, and related managed-sync metadata through the existing `state/store.py` seam only.
- Use `core/access.py` and `commands/_access_common.py` for effective-team validation and team-switch gating because those are already the validation seams for persona and team state.
- Make missing synced config behavior explicit.
- Make stale synced config behavior explicit, using sync metadata from `core/release_content.py` to detect whether the loaded manifest is out of date.
- Derive invalid-selected-team handling from the refreshed manifest comparison in `core/access.py` / `core/team_context.py`, not from duplicate team-selection persistence in `state/store.py`.

### UX/DX review checks for a later reviewer

- Check whether any currently direct logic should become a sync capability instead of being hardwired into command handlers.
- Check whether any commands should be split into subcommands or narrower flows if they grow too large.
- Check whether any conflict files or centrally maintained artifacts should move into the GHDP sync artifact path instead of staying local-only.
- Check whether prompting can be improved so the user does not need to memorize long command strings.
- Check whether shorthand forms and indexed selection prompts would reduce friction for team selection and sync-related operations.

### Acceptance criteria

- `team list` shows the approved teams from the synced source.
- `team use` and `team current` resolve against the synced team list.
- `tools validate` and `tools list` continue to work without schema drift.
- Missing or stale sync state is surfaced with a clear next step.
- Existing behavior stays stable because the toolset itself does not vary by team yet.
- Invalid selected-team state after sync refresh is detected and handled explicitly through config plus manifest comparison.

### Safety / rollback notes

- Do not collapse the fallback path until the synced artifact path is proven.
- If selected team state becomes invalid after a sync refresh, the user should be guided to reselect rather than left in a partial state.

## Phase 3: Validation, release, and orchestration trail

### Goal

Prove the change end to end, publish it through the normal release path, and leave a traceable orchestration record.

### File seams

- `platform-cli/tests/test_team_context.py`
- `platform-cli/tests/test_access_phase0.py`
- `platform-cli/tests/test_access_session_state.py`
- `platform-cli/tests/test_release_content.py`
- `platform-cli/tests/test_manifests_load.py` if a new loader-specific assertion file is needed
- `platform-cli/tests/test_sync_team_toolset.py` if a new sync-focused family is needed
- `platform-cli/README.md`
- `platform-cli/00_GHDP_CLI_COMMANDS_REFERENCE.toml`
- `.github/release-notes/notes.md`
- any release note or manifest docs that describe `toolset.json` as the source of truth

### Verification plan

- Add unit tests for manifest load precedence, missing synced config, stale refresh behavior, and team selection against the approved list.
- Add coverage for invalid selected team after refresh and the resulting revalidation path through access/team context.
- Add CLI tests for `team` and `tools` commands that consume the synced manifest.
- Validate locally with `pipx install --force .` and a clean GHDP context.
- Run post-install command checks against the locally installed binary.
- Update release notes before the release-candidate build.
- Create the prerelease at the very end of development, not per phase.
- After the prerelease is published, install from the release artifact and re-run the core validation commands.
- Create the PR only after code, tests, and release validation are done.
- Add PR and Jira comments noting the prerelease and validation outcome.

### Orchestration logging requirement

Record the orchestration plan in:

`C:\Users\Hi\Downloads\git-repos\tmp\agents-orchestrator-plans`

The log should include:

- the raw feature scope
- the phase breakdown
- the exact prompts used for each sub-agent or persona
- the iteration count for each feedback loop
- any design or UX findings that changed the blueprint
- the final implementation/test/release sequence

Use a timestamped filename that names this EPPE-7317 iteration specifically, not the entire feature branch.

### Acceptance criteria

- Tests cover the synced-manifest behavior and team flows.
- Release notes are updated before prerelease creation.
- The prerelease build is created only after implementation and validation are complete.
- Post-build install verification passes from the published artifact.
- PR and Jira comments reference the prerelease and validation result.
- The orchestration log is saved in the requested location with the requested contents.

### Safety / rollback notes

- If any release or post-release validation fails, stop before PR creation and fix the branch first.
- If the synced artifact path is unstable, keep the packaged fallback available until the release is verified.

### Optional future UX ideas

These are not must-change scope for EPPE-7317, but a later reviewer may want to consider them:

- indexed subcommand selection for long flows
- shorthand aliases for frequently used commands
- prompting improvements that reduce command memorization
- future capability-driven splits if commands become too large

## Likely test matrix

- approved team names resolve exactly as provided by Jira
- synced config present and current
- synced config missing
- synced config stale and refreshable
- selected team survives a sync refresh
- selected team becomes invalid after sync refresh
- `team list` / `team current` / `team use`
- `tools validate` / `tools list` / `tools status`
- manifest load from packaged fallback
- release-note update before prerelease build

## Open design questions for implementation

- Should the managed team-toolset artifact live under `.ghdp/manifests/` or a more explicit synced subpath?
- Should missing synced config fail hard for runtime commands, or fall back only for bootstrap and dev flows?
- Should stale synced config be detected by version metadata, content hash, or both?
- Should the refresh path emit a direct `ghdp sync ...` suggestion in command output?

## Non-goals for this phase

- team-specific tool variance
- new tool registry behavior
- reworking install/uninstall mechanics
- reorganizing the CLI folder structure
- changing the toolset schema shape
