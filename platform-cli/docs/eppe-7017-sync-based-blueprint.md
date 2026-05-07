# EPPE-7017 Sync-Based Blueprint

## Scope

This branch intentionally limits implementation to three areas:

1. Compatibility hardening for sync root-key handling.
2. Team-scoped sync capability restrictions.
3. Safe `CLAUDE.md` adoption in the tool install/setup flow.

Out of scope:

- Sync lifecycle redesign (`converge`, `cleanup`, `prune`, `reconcile`).
- Scheduler cleanup changes.
- Marketplace skill materialization or cleanup.
- New sync subcommands or new policy surfaces beyond team policy support.

## Phase 1: Compatibility Hardening

Current baseline already supports both `ghdp_root` and `ghdp_user_root` as aliases for `~/.ghdp`.

Planned work:

- Keep the existing resolver behavior unchanged unless tests expose a real gap.
- Add or tighten regression coverage around sync flows that load manifests using:
  - `ghdp_root`
  - `ghdp_user_root`
- Treat `ghdp_user_root` as the canonical forward path while preserving `ghdp_root` compatibility.

Primary files:

- `src/platform_cli/core/release_content.py`
- `tests/test_release_content.py`
- sync command tests that exercise manifest loading through CLI entrypoints

## Phase 2: Team-Scoped Sync Restrictions

Goal:

- Keep existing sync commands and behavior close to baseline.
- Restrict sync mutation commands so they only operate on capabilities allowed for the effective team.

Design rules:

- Team policy resolution stays in the access layer.
- `sync.py` only enforces and reports blocked capabilities.
- Full admin mode remains unrestricted.
- Non-admin, assumed-team, and team-token contexts honor team sync policy.

Planned policy support:

- Preferred:
  - `teams.<team>.sync.allow_capabilities`
  - `teams.<team>.sync.deny_capabilities`
- Legacy fallback:
  - `teams.<team>.allow_sync_capabilities`
  - `teams.<team>.deny_sync_capabilities`

Planned command behavior:

- `sync update`, `sync repair`, and `sync run` mutate only allowed capabilities.
- `sync check` surfaces blocked-by-policy capabilities clearly so users can tell policy issues from state drift.
- `sync scan` and `sync list` remain inventory/status oriented unless a small reporting improvement is needed.

Primary files:

- `src/platform_cli/core/access.py`
- `src/platform_cli/commands/sync.py`
- sync/admin/access tests

## Phase 3: Install-Flow `CLAUDE.md` Adoption

Goal:

- Ensure GHDP safely creates or adopts user-global `~/.claude/CLAUDE.md` during tool install/setup.

Design rules:

- All managed-block mutation stays in `user_global_agent_config.py`.
- `tools install` and `tools setup-agent-config` only orchestrate that helper.
- No second writer path for the same managed block.

Expected behavior:

- Missing file: create it with the GHDP managed block.
- Existing non-GHDP file: append the GHDP managed block without overwriting user content.
- Existing GHDP-managed file: replace only the GHDP managed block.

Primary files:

- `src/platform_cli/tools/user_global_agent_config.py`
- `src/platform_cli/tools/service.py`
- `src/platform_cli/commands/tools.py`
- `tests/test_user_global_agent_config.py`
- `tests/test_service_agent_config.py`

## Validation Plan

Automated:

- Regression tests for sync manifest root-key compatibility.
- Access and sync command tests for allowed and blocked team capability cases.
- Install/setup tests proving `CLAUDE.md` creation, append/adoption, and managed-block-only update behavior.

Manual:

- Pipx/source install smoke with the branch build.
- Targeted sync command validation with team policy fixtures.
- Claude install/setup smoke to confirm `CLAUDE.md` adoption behavior.

## Acceptance Criteria

- Sync manifests using either `ghdp_root` or `ghdp_user_root` load successfully through supported sync flows.
- Team policy can restrict sync mutation by capability without redesigning sync command behavior.
- Blocked sync capabilities are explained as policy-restricted in user-facing output.
- `CLAUDE.md` adoption happens through the install/setup flow and preserves non-GHDP content outside the managed block.
