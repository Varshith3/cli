# EPPE-7395 Blueprint

## Goal
Deliver one combined enhancement that:

- hardens `ghdp tools install` so failures across more phases are handled gracefully and summarized clearly
- improves Claude onboarding so Athena workgroup setup can be deferred and Claude launch can confirm or override the AWS profile at runtime

## Phase 1a: Tools Install Command And Manifest Resilience

### Scope
- Extend graceful handling beyond install execution into command-level preflight and post-`gh` refresh/re-resolution.
- Keep the command running and summarizing issues whenever a usable tool list can still be produced.

### Code Areas
- [commands/tools.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/commands/tools.py)
- a small helper module under `tools/` or command-adjacent helper for install-session bookkeeping and preflight result collection
- [core/errors.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/core/errors.py) only if stable new error codes are needed

### Key Behaviors
- Guard team-toolset resolution, manifest loading, team resolution, team tool expansion, and refresh/re-resolution.
- Keep command-scoped issues in `commands/tools.py` as a parallel summary model instead of forcing them into `ToolOnboardingStatus`.
- Convert recoverable preflight failures into summary items with `phase`, `code`, `detail`, and `next_action`.
- Preserve one explicit hard-stop path only when no usable tool list can be constructed at all.

### Test Strategy
- Extend [tests/test_tools_install_onboarding.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/tests/test_tools_install_onboarding.py)
- Extend [tests/test_tools_command_hooks.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/tests/test_tools_command_hooks.py)
- Add cases for:
  - failed team-toolset sync with fallback
  - manifest load failure
  - team resolution failure
  - detection failure classification
  - failed post-`gh` refresh that no longer crashes the run

### Risks And Rollback
- Keep the change additive and command-scoped rather than refactoring shared tool runtime layers.
- Avoid forcing command/manifests concerns into `tools/service.py`.
- Keep `commands/tools.py` as a thin controller for CLI wording and flow, while moving new bookkeeping into a helper so contributor complexity does not keep growing in one file.

## Phase 1b: Detection Contract Enrichment

### Scope
- Bring the first per-tool detection/preflight pass under a richer, but backward-compatible, summary model.
- Preserve current `detect_tool()` call sites while making detection failure modes observable.

### Code Areas
- [commands/tools.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/commands/tools.py)
- [tools/service.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/tools/service.py)

### Key Behaviors
- Add a new detailed detector or wrapper result object around `detect_tool()` instead of replacing the current `(installed, version)` contract.
- Distinguish these outcomes:
  - `not_installed`
  - `detect_cmd_failed`
  - `version_check_failed`
  - `detection_ambiguous`
- Keep phase-aware detection results visible in the install summary and state.

### Test Strategy
- Extend detection coverage in:
  - [tests/test_tools_install_onboarding.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/tests/test_tools_install_onboarding.py)
  - [tests/test_tools_command_hooks.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/tests/test_tools_command_hooks.py)
- Add focused cases for detection command failure classification and version-check ambiguity.

### Risks And Rollback
- Keep `detect_tool()` backward-compatible for current callers.
- Prefer an additive wrapper so rollback only requires stopping new summary usage, not undoing broad runtime changes.

## Phase 2: Optional Claude Athena Setup

### Scope
- Make Claude Athena workgroup setup optional during install/bootstrap.
- Support an explicit skipped or unset state.
- Add config commands to set, inspect, or clear the saved workgroup later.
- Treat the Athena workgroup mapping as managed or synced operational data, with local config as override or fallback.

### Code Areas
- [tools/athena_workgroup.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/tools/athena_workgroup.py)
- [tools/claude_auth.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/tools/claude_auth.py)
- [commands/config_cli.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/commands/config_cli.py)
- [core/config.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/core/config.py)
- [manifests/load.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/manifests/load.py)
- any existing synced-content source and release asset path used for the Claude Athena workgroup map

### Key Behaviors
- Change Athena resolution from “must return non-empty or fail” to “resolved or deferred”.
- Make the managed Athena workgroup map the operational source of truth when present, with local `claude.athena_workgroup` serving as an override or fallback.
- If mapping cannot be derived, interactive flow offers:
  - enter Athena workgroup now
  - skip for now and configure later
- Non-interactive flow records a deferred state instead of failing install.
- Keep the shared install status enum unchanged and store Claude-specific substate separately.
- Claude-specific substate may be `ok`, `deferred`, or `partial_ready`, but the surfaced installer result still maps onto existing shared buckets like `ready` or `action_required`.
- Mapping-load issues stay user-friendly and still allow manual entry or skip.

### Recommended Commands
- Add a dedicated config subcommand under `ghdp config` for Claude Athena workgroup management, aligned with current one-setting-per-command patterns.
- Lock the command shape to:
  - `ghdp config claude-athena-workgroup --value <name>`
  - `ghdp config claude-athena-workgroup --clear`
  - `ghdp config claude-athena-workgroup` for current-value display

### Test Strategy
- Extend [tests/test_claude_auth.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/tests/test_claude_auth.py)
- Add config command coverage for set, show, and clear
- Add cases for:
  - skip-now flow
  - unset config reuse
  - mapping load failure with user-friendly fallback
  - deferred onboarding summary

### Risks And Rollback
- Keep summary text explicit that Athena is deferred so Claude is not overstated as fully ready.
- Keep launch-time env handling tolerant of an unset workgroup.
- Update synced-content wiring and docs together if the Athena map source of truth changes, so repo data and runtime behavior do not drift again.

## Phase 3: Claude Env Prep Helper And Launch UX

### Scope
- Add a Claude-specific env-prep and launcher flow that can show the effective AWS profile and optionally let the user choose another one.
- Keep Codex behavior unchanged.

### Code Areas
- [commands/claude.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/commands/claude.py)
- [tools/claude_passthrough.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/tools/claude_passthrough.py) or a small adjacent launcher helper
- [tools/aws_profile.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/tools/aws_profile.py)
- [00_GHDP_CLI_COMMANDS_REFERENCE.toml](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/00_GHDP_CLI_COMMANDS_REFERENCE.toml)

### Key Behaviors
- Keep Claude on the shared `resolve_aws_profile()` order and only add confirmation or selection on top of the already-resolved effective profile.
- Do not special-case `aws.active_profile` ahead of repo or env scope.
- Keep `ghdp claude` pure passthrough.
- Land a separate GHDP-owned launcher surface for Claude, rather than attaching GHDP-owned flags to the passthrough command.
- Show the resolved profile and its source, then auto-continue by default.
- Only prompt to choose another profile when:
  - the resolved source is weak such as `default`
  - no active profile is configured
  - the user explicitly asks to choose
- Reuse the existing AWS profile picker when profile selection is needed.
- Apply the chosen profile only to Claude’s launch-time process environment.
- Do not add `claude.last_profile` as durable config.
- If convenience state is needed, keep it ephemeral in tool state only.

### Recommended Commands
- Add a dedicated sibling launcher surface such as:
  - `ghdp claude-launch --profile <name> -- <claude args>`
  - optionally `ghdp claude-launch --choose-profile -- <claude args>`
- Leave `ghdp claude` untouched as passthrough.

### Test Strategy
- Extend [tests/test_aws_profile.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/tests/test_aws_profile.py)
- Add a Claude command test file for:
  - default-profile confirm
  - decline-and-pick flow
  - explicit `--profile`
  - ephemeral chosen-profile launch behavior

### Risks And Rollback
- Keep the launcher changes thin and additive so rollback can preserve the current passthrough path untouched.
- Avoid creating two durable default-profile mental models for users.

## Cross-Cutting Contracts

- Extend the current install reporting model without creating a new framework.
- Keep command-scoped preflight issues in [commands/tools.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/commands/tools.py) as a parallel summary list.
- Keep [tools/service.py](/C:/Users/Hi/Downloads/git-repos/dp-tools-local-setup/platform-cli/src/platform_cli/tools/service.py) focused on per-tool runtime results.
- Minimum additive fields for new summary records:
  - `phase`
  - `outcome`
  - `code`
  - `next_action`
- Keep `detect_tool()` backward-compatible and add a richer wrapper or sibling result so detection failure modes survive into summary and state.
- Keep config and state schema backward-compatible and additive.
- Reasonable new keys:
  - `detection_status`
  - `detection_error_code`
  - `claude_onboarding_state`
  - `claude_athena_workgroup_source=skipped`
- Do not change team-toolset or tool-registry manifest schemas unless a tiny additive field is truly unavoidable.

## Out Of Scope

- Broad architecture refactors across layers
- Codex launch behavior changes
- Scheduler feature work beyond reusing the shared summary model
- Large manifest schema redesigns
