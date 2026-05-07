# Claude Tools Install Reference

## Purpose

This document is the technical reference companion to the Data Ops runbook. It explains the files, capabilities, precedence rules, and implementation ownership that sit behind Claude install and launch behavior in GHDP.

Use this document when you need to answer:

- which Git artifact GHDP is using
- where GHDP caches it locally
- which file wins if multiple sources exist
- which command owns which behavior
- where to inspect state after a failed install

## Core Commands

| Command | Purpose |
|---|---|
| `ghdp tools install --tool claude` | Install Claude and run Claude post-install bootstrap |
| `ghdp claude-launch` | Launch Claude with GHDP AWS-profile resolution and AWS SSO validation |
| `ghdp claude` | Plain Claude passthrough |
| `ghdp config claude-athena-workgroup` | Show, set, or clear the saved Athena workgroup |
| `ghdp sync check --capability claude-athena-workgroup-map` | Check whether the managed Athena mapping capability is available or up to date |
| `ghdp sync run --capability claude-athena-workgroup-map --auto-approve` | Refresh the managed Athena mapping explicitly |

## Git Artifact Capabilities

### 1. `claude-athena-workgroup-map`

Purpose:

- maps AWS account ID plus role name to the Athena workgroup GHDP should use for Claude

Current content-index entry:

- capability: `claude-athena-workgroup-map`
- version: `1.0.1`
- tag: `claude-athena-workgroup-map-v1.0.1`
- repo: `gh-org-data-platform/dp-tools-local-setup`
- manifest asset: `content-manifest.json`

Source and build wiring:

- source file: `src/platform_cli/resources/claude/athena-workgroup-map.json`
- release asset builder: [scripts/build_claude_athena_workgroup_release_assets.py](../scripts/build_claude_athena_workgroup_release_assets.py)
- content index source: [release-assets/content_index/content-index.json](../release-assets/content_index/content-index.json)

Release asset behavior:

- published file in the release: `athena-workgroup-map.json`
- sync target filename on the user machine: `claude-athena-workgroup-map.managed.json`
- target directory: `~/.ghdp/policies/`

### 2. `claude-skills-aws`

Purpose:

- syncs the Claude AWS read-only skill bundle into the user's Claude skills directory

Current content-index entry:

- capability: `claude-skills-aws`
- version: `1.0.0`
- tag: `claude-skills-aws-v1.0.0`
- repo: `gh-org-data-platform/dp-tools-local-setup`

Local target:

- `~/.claude/skills`

Implementation:

- [src/platform_cli/tools/claude_skill_sync.py](../src/platform_cli/tools/claude_skill_sync.py)

### 3. `ghdp-team-toolset`

Purpose:

- controls which tools belong to each team and which version policy applies

Why it matters for Claude:

- when the operator installs tools by team, this capability determines whether Claude is included

Current content-index entry:

- capability: `ghdp-team-toolset`
- version: `1.0.4`
- tag: `ghdp-team-toolset-v1.0.4`

## Local Paths And What They Mean

| Path | Meaning |
|---|---|
| `~/.ghdp/config.json` | GHDP configuration, including `claude.athena_workgroup` |
| `~/.ghdp/state/state.json` | GHDP per-tool state, including Claude bootstrap details |
| `~/.ghdp/policies/claude-athena-workgroup-map.managed.json` | managed Athena mapping cache used by Claude install |
| `~/.ghdp/policy/claude-athena-workgroup-map.json` | optional user override mapping |
| `~/.claude/skills/` | Claude skill target directory |
| `~/.claude/CLAUDE.md` | GHDP-managed Claude instruction block target |

## Athena Mapping Source Precedence

When GHDP loads the Athena mapping itself, the precedence is:

1. environment override:
   - `GHDP_CLAUDE_ATHENA_WORKGROUP_MAP_PATH`
2. user override file:
   - `~/.ghdp/policy/claude-athena-workgroup-map.json`
3. managed cached file:
   - `~/.ghdp/policies/claude-athena-workgroup-map.managed.json`
4. packaged primary file:
   - `src/platform_cli/resources/claude/athena-workgroup-map.json`
5. packaged backup file:
   - `src/platform_cli/resources/claude/athena-workgroup-map.backup.json`

Important nuance:

- before Claude install resolves the workgroup, GHDP now checks whether the managed cached file exists
- if it is missing, GHDP attempts to sync `claude-athena-workgroup-map`
- if sync cannot provide the file, the loader still falls back to the packaged files

## Athena Workgroup Resolution Precedence

The Athena workgroup value used for Claude is resolved in this order:

1. `DP_AWS_ATHENA_WORKGROUP`
2. internal mapping derived from AWS identity
3. saved config key `claude.athena_workgroup`
4. interactive prompt
5. deferred or skip-for-now state

This means:

- an environment variable wins immediately
- automatic AWS identity mapping beats a previously saved config value
- saved config is the fallback if mapping does not match

## AWS Profile Resolution For `ghdp claude-launch`

The launch flow lives in:

- [src/platform_cli/tools/claude_passthrough.py](../src/platform_cli/tools/claude_passthrough.py)

Behavior summary:

- if `--profile` is passed, GHDP uses that profile directly
- if `--choose-profile` is passed, GHDP opens the picker directly
- if neither is passed, GHDP resolves the effective profile and asks the user to confirm it
- after the final profile is chosen, GHDP validates AWS SSO configuration and token state before launching Claude

## Claude Install Ownership By Module

| Module | Responsibility |
|---|---|
| `commands/tools.py` | CLI entrypoint and orchestration for tool install |
| `tools/service.py` | install execution, detection, onboarding summary, and post-install routing |
| `tools/claude_auth.py` | Claude-specific post-install bootstrap |
| `tools/athena_workgroup.py` | workgroup resolution and prompt or defer logic |
| `tools/claude_athena_workgroup_assets.py` | cache-first managed mapping availability and sync-on-missing |
| `tools/claude_passthrough.py` | `ghdp claude-launch` logic |
| `tools/claude_skill_sync.py` | Claude skill bundle sync |
| `commands/config_cli.py` | `ghdp config claude-athena-workgroup` command |

## Claude Install State Fields Worth Inspecting

Claude writes runtime details into `~/.ghdp/state/state.json` under `tools.claude`.

Useful fields include:

- `claude_exe`
- `claude_version`
- `claude_health_state`
- `claude_health_status`
- `claude_aws_profile`
- `claude_athena_workgroup`
- `claude_athena_workgroup_source`
- `claude_athena_workgroup_mapping_source`
- `claude_athena_workgroup_configured`
- `claude_athena_map_local_status`
- `claude_athena_map_target_path`
- `claude_athena_map_latest_tag`
- `claude_athena_map_used_cached`
- `claude_skill_sync_source`
- `claude_skill_sync_release_tag`

## Team Install Versus Direct Claude Install

### Direct install

```powershell
ghdp tools install --tool claude
```

Behavior:

- does not need team toolset resolution to know Claude exists
- uses the Claude registry entry directly

### Team-driven install

```powershell
ghdp tools install --team <team>
```

Behavior:

- uses `ghdp-team-toolset` to decide which tools should be installed
- Claude is installed only if that team toolset includes Claude

## Release Pages And Artifact References

All Claude-related managed artifacts are published in:

- `https://github.com/gh-org-data-platform/dp-tools-local-setup/releases`

Useful release URLs:

- content index:
  - `https://github.com/gh-org-data-platform/dp-tools-local-setup/releases/tag/content-index-latest`
- Claude Athena mapping:
  - `https://github.com/gh-org-data-platform/dp-tools-local-setup/releases/tag/claude-athena-workgroup-map-v1.0.1`
- Claude skills:
  - `https://github.com/gh-org-data-platform/dp-tools-local-setup/releases/tag/claude-skills-aws-v1.0.0`
- team toolset:
  - `https://github.com/gh-org-data-platform/dp-tools-local-setup/releases/tag/ghdp-team-toolset-v1.0.4`

## Known Good Operator Checks

To confirm the local machine is in a healthy Claude-ready state:

```powershell
ghdp --version
ghdp sync check --capability claude-athena-workgroup-map
ghdp config claude-athena-workgroup
ghdp claude-launch -- --help
```

## Safe Mental Model For Data Ops

Use this model when explaining behavior:

- GHDP first tries to use centrally managed data
- if managed data was already cached locally, GHDP uses the cache
- if managed data is missing, GHDP tries to sync it
- if sync cannot provide it, GHDP falls back to packaged local data where supported
- if Claude still cannot resolve the Athena workgroup automatically, the user can enter it manually or skip for now
