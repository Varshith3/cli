# Claude Tools Install Data Ops Runbook

## Purpose

This runbook is the primary operational guide for Data Ops when helping an end user install and launch Claude Code through GHDP.

Use this document when:

- the user is installing Claude for the first time
- the user is reinstalling Claude after cleanup
- the user is launching Claude through `ghdp claude-launch`
- the user hits Athena workgroup, AWS profile, sync, or summary issues during Claude setup

Use the companion documents when needed:

- [Claude Tools Install Troubleshooting](./claude-tools-install-troubleshooting.md)
- [Claude Tools Install Reference](./claude-tools-install-reference.md)

## Audience

This document is written for the Data Ops team, not for end users. It assumes the operator may need to:

- drive the install with the user live
- explain GHDP prompts
- inspect local GHDP cache, config, and state
- decide whether to continue, retry, skip, or escalate

## Scope

This runbook covers:

- `ghdp tools install --tool claude`
- team-driven tool install when Claude is part of the team toolset
- `ghdp claude-launch`
- Claude Athena workgroup resolution
- AWS profile and AWS SSO behavior for Claude launch
- Git artifact usage relevant to Claude install

This runbook does not cover:

- Claude product usage after launch
- Codex setup, except where behavior differs from Claude
- advanced GitHub sync internals beyond what Data Ops needs operationally

## Operational Model

There are three major Claude-related flows in GHDP:

| Flow | Command | What it does |
|---|---|---|
| Install Claude | `ghdp tools install --tool claude` | Installs Claude, checks AWS readiness, resolves Athena workgroup, writes local Claude environment, syncs Claude skills, and prepares the user for launch |
| Configure Athena later | `ghdp config claude-athena-workgroup ...` | Shows, sets, or clears the saved Claude Athena workgroup in GHDP config |
| Launch Claude via GHDP | `ghdp claude-launch` | Resolves the AWS profile for this launch, confirms or changes the profile, validates AWS SSO token state, resolves the Athena workgroup, and launches Claude |

There is also a plain passthrough command:

| Flow | Command | What it does |
|---|---|---|
| Raw Claude passthrough | `ghdp claude` | Passes directly through to the Claude CLI without GHDP launch-time AWS profile selection |

## How GHDP Decides What To Do

### Install path

During `ghdp tools install --tool claude`, GHDP does this in order:

1. Detect whether Claude is already installed.
2. Install or upgrade Claude if needed.
3. Run AWS readiness/bootstrap for the effective AWS profile.
4. Ensure the Claude Athena workgroup mapping is available:
   - use cached managed file if present
   - sync the managed mapping if cache is missing
   - fall back to packaged mappings if sync cannot provide the file
5. Resolve the Athena workgroup for Claude.
6. Write GHDP-managed Claude environment to the user profile.
7. Sync the Claude AWS read-only skill bundle.
8. Run Claude health verification.
9. Offer same-session launch on Windows when applicable.

### Athena workgroup resolution order

GHDP resolves Claude's Athena workgroup in this order:

1. `DP_AWS_ATHENA_WORKGROUP` environment variable
2. internal mapping derived from AWS account ID and role name
3. saved GHDP config value `claude.athena_workgroup`
4. manual entry prompt
5. skip for now or deferred state

Important note:

- saved GHDP config is used only after automatic AWS identity mapping does not resolve a match

### Launch path

During `ghdp claude-launch`, GHDP does this in order:

1. Resolve the effective AWS profile.
2. If the operator did not pass `--profile`, confirm the profile with the user.
3. If the user declines, show the AWS profile picker.
4. Validate AWS SSO configuration and token state for the chosen profile.
5. Resolve the Athena workgroup for the chosen profile.
6. Launch Claude with session-only environment variables.

## Git Artifacts Used By Claude Install

Claude install relies on these Git-backed GHDP artifacts:

| Capability | Current tag | Why it matters |
|---|---|---|
| `claude-athena-workgroup-map` | `claude-athena-workgroup-map-v1.0.1` | Maps AWS account ID and role name to the Athena workgroup GHDP should use for Claude |
| `claude-skills-aws` | `claude-skills-aws-v1.0.0` | Syncs the Claude AWS read-only skill bundle into `~/.claude/skills` |
| `ghdp-team-toolset` | `ghdp-team-toolset-v1.0.4` | Decides whether Claude is part of a team-driven tools install and what version policy applies |
| `content-index` | `content-index-latest` | Central index GHDP uses to locate capability repo, tag, and manifest asset |

These artifacts are published from:

- repo: `gh-org-data-platform/dp-tools-local-setup`
- releases page: `https://github.com/gh-org-data-platform/dp-tools-local-setup/releases`

## Local Files Operators Should Know

| Path | Purpose |
|---|---|
| `~/.ghdp/config.json` | GHDP config, including saved `claude.athena_workgroup` |
| `~/.ghdp/state/state.json` | GHDP tool and runtime state, including Claude post-install metadata |
| `~/.ghdp/policies/claude-athena-workgroup-map.managed.json` | Cached managed Athena mapping used by Claude install |
| `~/.ghdp/policy/claude-athena-workgroup-map.json` | Optional user override Athena mapping |
| `~/.claude/skills/` | Claude skill bundle destination |
| `~/.claude/CLAUDE.md` | GHDP-managed Claude global instructions block |

## Happy Path: First-Time Claude Install

### Recommended operator sequence

1. Confirm GHDP itself is installed and on the expected prerelease build:

```powershell
ghdp --version
```

2. Check whether Claude's Athena mapping cache is already present:

```powershell
ghdp sync check --capability claude-athena-workgroup-map
```

3. Start the install:

```powershell
ghdp tools install --tool claude
```

4. Guide the user through any AWS, browser, or Athena prompts.

5. Validate post-install behavior:

```powershell
ghdp config claude-athena-workgroup
ghdp claude-launch -- --help
```

### What "good" looks like

A successful Claude install should usually end with:

- no stale pre-install detect warning in the final summary
- visible installer output rather than a long frozen-looking blank screen
- Athena workgroup either:
  - auto-resolved
  - entered manually
  - skipped for now with clear follow-up guidance
- Claude skills synced successfully
- Claude marked ready, or action-required only for a legitimate deferred follow-up

## Athena Workgroup Scenarios

### Scenario A: Athena workgroup auto-resolves

What happens:

- GHDP reads the AWS identity
- GHDP matches account ID and role name through the internal Athena mapping
- GHDP saves the resolved workgroup into GHDP config
- install continues without manual Athena input

Operator action:

- let the flow continue
- no manual workgroup entry is needed

### Scenario B: Mapping is not cached yet

What happens:

- GHDP checks for `~/.ghdp/policies/claude-athena-workgroup-map.managed.json`
- if the file is missing, GHDP attempts to sync `claude-athena-workgroup-map`
- if sync succeeds, GHDP uses the synced managed file

Operator action:

- this is normal on a first-time machine
- do not treat a one-time mapping sync as a failure

### Scenario C: Sync cannot provide the mapping

What happens:

- GHDP falls back to packaged local mapping files
- install still continues unless later Athena resolution itself cannot finish automatically and the user must choose

Operator action:

- tell the user GHDP is continuing with packaged fallback data
- continue install unless the later Athena prompt requires manual input and the user cannot provide it

### Scenario D: No mapping match exists

What happens:

- GHDP knows the AWS identity
- GHDP cannot find a matching Athena workgroup in the mapping
- GHDP prompts the user to either:
  - enter an Athena workgroup now
  - skip for now

Operator action:

- if the correct workgroup is known, enter it
- if the correct workgroup is not known, use skip-for-now and continue

### Scenario E: User skips Athena workgroup for now

What happens:

- GHDP stores an empty deferred value in config
- install continues without treating the install as a hard failure
- later launch or later install attempts can still be completed once the correct workgroup is known

Operator action:

- reassure the user that skipping is supported behavior
- record the follow-up needed
- later set the value with:

```powershell
ghdp config claude-athena-workgroup --value <workgroup>
```

## AWS Profile Scenarios For Claude Launch

### Scenario A: User runs `ghdp claude-launch`

Expected behavior:

- GHDP shows the resolved AWS profile and its source
- GHDP asks whether to use that profile for this Claude launch
- if user says yes, GHDP validates AWS SSO token state and proceeds
- if user says no, GHDP opens the AWS profile picker

Operator action:

- confirm the displayed profile is the one the user intends
- if not, tell the user to choose a different profile when prompted

### Scenario B: User runs `ghdp claude-launch --profile <name>`

Expected behavior:

- GHDP uses that explicit profile
- GHDP does not ask the confirmation question
- GHDP still validates AWS SSO token state before launch

Operator action:

- use this when the operator already knows the correct profile

### Scenario C: User runs `ghdp claude-launch --choose-profile`

Expected behavior:

- GHDP skips the confirmation question
- GHDP directly opens the picker
- GHDP validates token state for the selected profile

Operator action:

- use this when the currently resolved profile is expected to be wrong or unclear

## Exact Commands Data Ops Should Keep Handy

### Install

```powershell
ghdp tools install --tool claude
```

### Check cached mapping capability

```powershell
ghdp sync check --capability claude-athena-workgroup-map
```

### Force refresh the mapping manually

```powershell
ghdp sync run --capability claude-athena-workgroup-map --auto-approve
```

### Inspect saved Athena workgroup

```powershell
ghdp config claude-athena-workgroup
```

### Set Athena workgroup later

```powershell
ghdp config claude-athena-workgroup --value <workgroup>
```

### Clear Athena workgroup

```powershell
ghdp config claude-athena-workgroup --clear
```

### Launch Claude with guided profile handling

```powershell
ghdp claude-launch
```

### Launch Claude with explicit profile

```powershell
ghdp claude-launch --profile <profile>
```

### Pick a profile first, then launch

```powershell
ghdp claude-launch --choose-profile
```

### Refresh Claude global instructions only

```powershell
ghdp tools setup-agent-config --tool claude
```

## Post-Install Validation Checklist

Run these after a user reports install completed:

```powershell
ghdp config claude-athena-workgroup
ghdp claude-launch -- --help
```

Sign-off checklist:

- Claude CLI is installed and callable
- Athena workgroup is either configured or intentionally deferred
- Claude launch confirms or picks the correct AWS profile
- AWS SSO token validation or login runs before launch if needed
- Claude global instructions exist at `~/.claude/CLAUDE.md`
- Claude skill bundle exists under `~/.claude/skills`

## Safe Repeat Or Retest Flow

If Data Ops needs to rerun setup cleanly without doing a deep uninstall:

1. Inspect the saved Athena value:

```powershell
ghdp config claude-athena-workgroup
```

2. Clear it only if needed:

```powershell
ghdp config claude-athena-workgroup --clear
```

3. Refresh the managed Athena mapping if needed:

```powershell
ghdp sync run --capability claude-athena-workgroup-map --auto-approve
```

4. Re-run install:

```powershell
ghdp tools install --tool claude
```

Use a deeper cleanup only when the machine must simulate a true first-time install. See the troubleshooting guide.

## When Data Ops Should Escalate

Escalate if:

- AWS identity is clearly valid but the mapping result is still incorrect after confirming the latest managed mapping
- Claude install succeeds but repeated health checks still fail
- `ghdp claude-launch` is choosing the right AWS profile and valid token but Claude still cannot start
- packaged fallback data appears broken or invalid
- the managed mapping capability or content index appears inconsistent across multiple machines
- the issue reproduces on both Windows and macOS with the same GHDP build

## Operator Notes

- `ghdp claude` is a passthrough command; do not expect launch-time AWS profile selection there.
- `ghdp claude-launch` is the supported operator-friendly launch command.
- Athena skip-for-now is not a failure by itself.
- A missing cached Athena mapping file on a first-time machine is normal.
- If install summary shows a real issue, trust the summary phase and next-action guidance rather than guessing from transient earlier output.
