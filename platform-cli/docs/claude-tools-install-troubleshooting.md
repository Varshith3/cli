# Claude Tools Install Troubleshooting

## How To Use This Guide

Find the symptom that best matches what the user sees. Each section gives:

- what the symptom usually means
- how to confirm it
- what to do next
- when to escalate

Use this together with:

- [Claude Tools Install Data Ops Runbook](./claude-tools-install-data-ops-runbook.md)
- [Claude Tools Install Reference](./claude-tools-install-reference.md)

## Quick Triage Matrix

| Symptom | Most likely cause | First action |
|---|---|---|
| Claude install appears frozen | installer output is not being watched closely or operator is expecting frequent prompts | wait for visible installer output; verify the terminal is still active |
| `claude [detect] Detection command failed before install` but install later succeeds | stale pre-install detection issue from an older build, or the machine is not on the updated prerelease | confirm GHDP version and rerun on the current prerelease |
| Athena mapping is not found | AWS identity did not match mapping, cache is missing, or mapping source is unavailable | inspect cached mapping and decide whether to enter workgroup or skip |
| User does not know the Athena workgroup | mapping did not resolve and operator does not have the value yet | use skip-for-now and continue |
| `ghdp claude-launch` uses the wrong profile | resolved global/env profile is not the one the user wants | decline the confirmation and pick another profile |
| Claude launch triggers AWS login | AWS token is expired or missing for the chosen profile | complete login and relaunch |
| Claude installed but `claude-launch` still fails | Claude binary or runtime health check is failing | verify Claude on PATH and inspect tool state |
| Sync check succeeds but install still does not resolve Athena correctly | cached managed mapping may be stale or the role/account pair is absent from the mapping | refresh the capability and compare identity to mapping |

## Scenario 1: Install Looks Frozen

### What this usually means

The Claude installer is still running, but the operator expects frequent prompt output. On current GHDP builds, Claude installer output should stream instead of looking stuck.

### What to check

```powershell
ghdp --version
ghdp tools install --tool claude
```

Confirm:

- the user is on the current prerelease build
- the terminal is still active
- installer output is visible

### Resolution

- if the machine is on an older GHDP build, move to the latest prerelease
- if there is still no output for an unusually long time, stop and retry once
- if it reproduces, capture the terminal output and escalate

## Scenario 2: Detection Warning Appears Before Success

### What this usually means

This usually indicates an older GHDP build where the Claude detect probe could emit a warning before the install later succeeded.

### What to check

```powershell
ghdp --version
```

### Resolution

- confirm the machine is on the updated prerelease
- rerun:

```powershell
ghdp tools install --tool claude
```

- if the final summary still shows a stale detect warning after a healthy install on the current build, escalate

## Scenario 3: Athena Mapping Does Not Auto-Resolve

### What this usually means

One of these is true:

- the cached managed mapping file is missing
- the mapping sync could not run or could not provide the file
- the AWS account ID and role name do not match any mapping entry

### What to check

```powershell
ghdp sync check --capability claude-athena-workgroup-map
ghdp config claude-athena-workgroup
```

If needed, inspect:

- `~/.ghdp/policies/claude-athena-workgroup-map.managed.json`
- `~/.ghdp/config.json`

### Resolution

1. If the cached managed file is missing, run:

```powershell
ghdp sync run --capability claude-athena-workgroup-map --auto-approve
```

2. Retry Claude install.

3. If there is still no mapping match:
   - enter the workgroup manually if known
   - otherwise skip for now and continue

### Escalate when

- the role/account pair should definitely exist in the mapping but still does not resolve
- multiple users on the same account/role pair hit the same failure on the latest mapping

## Scenario 4: User Does Not Know The Athena Workgroup

### What this usually means

The internal mapping did not resolve, and the user does not have the manual workgroup value yet.

### Resolution

Use skip-for-now during install. This is supported behavior.

Later, once the correct value is known:

```powershell
ghdp config claude-athena-workgroup --value <workgroup>
```

Then verify:

```powershell
ghdp config claude-athena-workgroup
ghdp claude-launch -- --help
```

## Scenario 5: Wrong AWS Profile Is Being Used For Claude Launch

### What this usually means

GHDP correctly resolved an active profile from env, repo, or global config, but the user wants a different one for this Claude session.

### What to check

```powershell
ghdp claude-launch
```

Observe the line:

- `Claude launch AWS profile: <name> (source=<source>)`

### Resolution

- if the displayed profile is wrong, answer `No` to the confirmation prompt
- choose the correct profile from the picker

If the operator already knows the desired profile, run:

```powershell
ghdp claude-launch --profile <profile>
```

## Scenario 6: AWS SSO Token Is Expired Or Missing

### What this usually means

The chosen AWS profile is valid as a profile name, but its SSO login is not currently usable.

### What to check

```powershell
ghdp claude-launch
```

Expected behavior:

- GHDP should tell the user the token is missing or expired
- GHDP should run login before launching Claude

### Resolution

- let the login flow complete
- rerun the launch if the browser flow was interrupted

### Escalate when

- the same profile cannot complete `aws sso login` outside Claude launch either
- AWS SSO configuration itself appears broken or incomplete

## Scenario 7: Claude Installed But Launch Still Fails

### What this usually means

One of these is true:

- Claude binary is not visible in the current session
- the post-install health check failed
- a shell/profile refresh issue is preventing the current terminal from seeing Claude

### What to check

```powershell
ghdp claude-launch -- --help
```

If needed, inspect:

- `~/.ghdp/state/state.json`

Look for Claude state fields such as:

- `claude_exe`
- `claude_health_state`
- `claude_health_status`

### Resolution

- open a fresh terminal and retry
- rerun:

```powershell
ghdp tools install --tool claude
```

- if the health check still fails, escalate with the Claude state details

## Scenario 8: Mapping Sync Fails

### What this usually means

GHDP could not pull the managed Athena mapping capability from GitHub.

### What to check

```powershell
ghdp sync check --capability claude-athena-workgroup-map
ghdp sync run --capability claude-athena-workgroup-map --auto-approve
```

### Resolution

- if sync fails during install, GHDP should still be able to continue with packaged fallback mapping
- continue install unless the later Athena resolution prompt blocks progress because the user cannot provide the workgroup

### Escalate when

- the capability is missing from the content index
- the release asset is unavailable
- sync fails across multiple machines

## Scenario 9: User Previously Skipped Athena And Now Wants To Set It

### Resolution

```powershell
ghdp config claude-athena-workgroup --value <workgroup>
ghdp config claude-athena-workgroup
ghdp claude-launch -- --help
```

No reinstall is required just to set the saved Athena workgroup.

## Scenario 10: Need A Fresh Claude Retest

### Light reset

Use this first:

```powershell
ghdp config claude-athena-workgroup --clear
ghdp sync run --capability claude-athena-workgroup-map --auto-approve
ghdp tools install --tool claude
```

### Deep reset

Use a deep reset only when the machine must behave like a near-first-time Claude install.

Typical locations to inspect and clean carefully:

- `~/.ghdp/config.json`
- `~/.ghdp/state/state.json`
- `~/.ghdp/policies/claude-athena-workgroup-map.managed.json`
- `~/.claude/`
- `~/.local/bin/claude` or `~/.local/bin/claude.exe`

Deep cleanup should be coordinated carefully because it removes working state.

## Escalation Checklist

Before escalating, collect:

- GHDP version
- OS
- command used
- final install summary
- whether Athena was auto-resolved, manually entered, or skipped
- output of:

```powershell
ghdp config claude-athena-workgroup
ghdp sync check --capability claude-athena-workgroup-map
```

- whether `ghdp claude-launch` showed the expected AWS profile
- whether AWS SSO login completed

