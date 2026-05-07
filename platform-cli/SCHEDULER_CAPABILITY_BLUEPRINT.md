## Scheduler Capability Blueprint

### Objective

Reshape `ghdp schedule` so all desired task definitions live in release-backed Git artifacts, are installed through the sync capability model, and then reconcile into the local OS scheduler. Phase 1 keeps Windows Task Scheduler as the only provider implementation, but freezes a capability contract that can extend to launchd, cron, and systemd timers later.

### Why This Blueprint Changed

The first scheduler iteration proved out the Windows wrapper and runtime logging flow, but it treated repo-local `.ghdp/capabilities/scheduler/` as the authored source of truth. That no longer matches the intended design. The corrected requirement is:

- all task-specific config lives in Git artifacts
- those artifacts are grouped under one scheduler capability
- the capability is distributed through the sync/release-content path
- local machine state is observational only

### Phase 1 Scope

Phase 1 delivers:

- one Git-owned scheduler source bundle rooted at `platform-cli/release-assets/background_scheduler/`
- one release-backed scheduler capability installed into `.ghdp/capabilities/scheduler/` per repo
- one defaults artifact plus one task artifact per task
- manifest loading and validation in `platform_cli.manifests`
- scheduler reconciliation and provider behavior in `platform_cli.tools`
- explicit Windows scheduling policies for the most important runtime controls
- updated CLI docs and release notes for the new contract

Phase 1 does not deliver:

- launchd implementation
- cron implementation
- systemd timer implementation
- CI or service-account scheduling
- end-user authored personal schedules outside the centrally managed capability
- automatic repair hooks on arbitrary `ghdp` commands

### Source Of Truth

Desired state authoring:

- `platform-cli/release-assets/background_scheduler/capability.json`
- `platform-cli/release-assets/background_scheduler/defaults.json`
- `platform-cli/release-assets/background_scheduler/tasks/<task-id>.json`

Desired state installation:

- `.ghdp/capabilities/scheduler/capability.json`
- `.ghdp/capabilities/scheduler/defaults.json`
- `.ghdp/capabilities/scheduler/tasks/<task-id>.json`

Observed runtime state only:

- `~/.ghdp/state/state.json`
- `~/.ghdp/schedule/wrappers/*.ps1`
- `~/.ghdp/schedule/logs/*.jsonl`

### Capability Layout

```text
platform-cli/
  release-assets/
    background_scheduler/
      capability.json
      defaults.json
      tasks/
        sync-run-background.json
```

Naming conventions:

- capability id: `background-scheduler`
- task ids: lowercase kebab-case, verb-noun oriented
- one task per file for clean Git review and future additions

### Phase 1 Artifact Contract

`capability.json`

- capability metadata
- schema version
- display name and description
- artifact layout hints such as task directory and defaults file

`defaults.json`

- default platform list
- default trigger values
- default execution policy values
- default condition values
- default run-context values

`tasks/<task-id>.json`

- stable task id
- description
- enabled and required flags
- command definition
- trigger config
- execution policy overrides
- condition overrides
- run-context overrides
- platform list

### Task Schema For Phase 1

Each task resolves to this effective model after defaults are merged:

```json
{
  "id": "sync-run-background",
  "description": "Keep GHDP capability content in sync",
  "enabled": true,
  "required": true,
  "platforms": ["windows", "darwin", "linux"],
  "command": {
    "type": "ghdp",
    "args": ["sync", "run", "--auto-approve"]
  },
  "trigger": {
    "type": "interval",
    "minutes": 360,
    "random_delay_minutes": 5
  },
  "execution": {
    "timeout_minutes": 15,
    "overlap_policy": "skip",
    "catch_up_after_missed_run": true,
    "retry_on_failure": {
      "enabled": true,
      "minutes": 15,
      "max_attempts": 3
    }
  },
  "conditions": {
    "require_network": true,
    "allow_on_battery": true,
    "stop_on_battery": false,
    "idle_only": false,
    "wake_machine": false
  },
  "run_context": {
    "mode": "user_session",
    "elevated": false,
    "hidden": false
  }
}
```

### Recommended Default Values

Generic defaults:

- interval: `60` minutes
- random delay: `5` minutes
- timeout: `15` minutes
- overlap policy: `skip`
- catch up after missed run: `true`
- retry enabled: `true`
- retry interval: `15` minutes
- retry max attempts: `3`
- require network: `false`
- allow on battery: `true`
- stop on battery: `false`
- idle only: `false`
- wake machine: `false`
- run context mode: `user_session`
- elevated: `false`
- hidden: `false`

Task-specific overrides are expected for jobs like sync or log delivery that truly require network access.

### Windows Provider Contract

Phase 1 Windows support should explicitly manage:

- description
- repetition interval
- random delay
- start-when-available behavior
- execution time limit
- overlap policy
- retry-on-failure count and interval
- allow-on-battery and stop-on-battery
- require-network

Windows provider should continue to:

- generate a wrapper script per task under `~/.ghdp/schedule/wrappers/`
- keep the scheduled action short and quoting-safe
- query task XML for drift detection
- register tasks through PowerShell ScheduledTasks cmdlets and verify drift through task XML

Phase 1 Windows support does not promise:

- idle-only execution
- service-account or logged-out execution
- elevated execution

### CLI Surface For Phase 1

Target runtime command family:

- `ghdp sync run --capability background-scheduler --repo-root <path> --auto-approve`
- `ghdp schedule list`
- `ghdp schedule check`
- `ghdp schedule apply`
- `ghdp schedule repair`
- `ghdp schedule remove`
- hidden `ghdp schedule run-job`

Behavior changes from the first iteration:

- desired task definitions come from synced release-backed capability assets
- `ghdp schedule` is a reconciler/runtime surface, not a repo authoring surface
- contributor authoring happens in `platform-cli/release-assets/background_scheduler/`
- list and reconciliation flows operate on the synced installed capability task set

### Required Task Semantics

For `required=true` tasks in Phase 1:

- Git artifacts remain the only source of truth
- local removal through `schedule remove` is allowed for troubleshooting, but `schedule check` must report the task as missing and `schedule repair` must restore it
- if a required task is disabled in Git, that Git artifact state wins and GHDP must not silently re-enable it locally

### Local Identity And Runtime Cleanup

Machine-local task identity must be derived from:

- capability id
- task id
- repo identity hash derived from the resolved repo path

That identity should be used consistently for:

- provider task names
- wrapper script filenames
- runtime log filenames
- scheduler state keys

Minimum runtime cleanup rules for Phase 1:

- `schedule remove` unregisters the provider task and deletes the matching wrapper script
- runtime logs are retained as observational evidence and are not deleted by default
- renamed or removed Git task artifacts appear as local drift until explicitly removed or repaired

### Architecture Placement

This phase should align to repo architecture as follows:

- `commands/schedule.py` keeps Typer UX, prompts, and confirmations only
- `manifests/scheduler.py` loads, validates, and resolves scheduler capability artifacts
- `tools/scheduler.py` handles job preview, reconciliation, runtime logs, and state
- `tools/scheduler_windows.py` handles Windows provider registration and query behavior
- `exec/runner.py` remains the only subprocess boundary

### Validation Plan

Required validation before release:

- targeted pytest coverage for manifest loading, defaults merge, command UX, and Windows provider drift matching
- build validation with `python -m build`
- local install validation with `pipx install --force .`
- manual Windows task creation, inspection, run, and removal
- updated `.github/release-notes/notes.md`

### Phase Roadmap

Phase 2:

- add launchd and cron providers behind the same task contract
- add health-oriented doctor/reporting surfaces
- add a cleaner publisher path for scheduler capability asset releases and content-index updates

Phase 3:

- add service-account and non-interactive host support
- add richer trigger types like startup, logon, daily, and weekly
- add automatic repair hooks and stronger policy enforcement for mandatory jobs
