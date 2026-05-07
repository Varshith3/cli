# GHDP CLI Architecture (Code Philosophy + Folder Guide)

This document is the **source of truth** for how GHDP CLI is structured today, why it is structured this way, and what rules contributors (and coding agents) must follow to keep the design consistent.

> If you are changing structure, adding folders, or moving logic across layers: **read this fully first**.

---

## Goals of this architecture

1. **Predictability:** anyone can find where code should live.
2. **Safe extensibility:** new tools/commands can be added with minimal risk.
3. **Consistent UX:** consistent error handling, output formatting, and state behavior.
4. **Automation-friendly:** works in interactive and `--non-interactive` modes.
5. **Manifest-driven:** tools/teams are defined in manifests (data); code implements the engine.

---

## High-level mental model

GHDP is split into layers:

- **CLI Surface (commands):** parse args, call services, print results.
- **Core (cross-cutting):** shared context, errors, output helpers, telemetry, update checks.
- **Manifests (data + validation):** load/validate/resolve “what should be installed” per team.
- **Tools engine (runtime ops):** detect/install/upgrade/uninstall tools; OS-specific handling.
- **Exec (process runner):** one place to run subprocesses consistently.
- **State (persistence):** store tool state + timestamps + last actions.
- **Resources (bundled config):** JSON manifests shipped with the CLI.

---

## Execution flow (typical)

### Example: `ghdp tools install --team X --tool Y`

1. `src/platform_cli/cli.py`
   - Initializes global CLI context (`cli_ctx`)
   - Registers subcommands from `platform_cli/commands/*`
2. `platform_cli/commands/*`
   - Parses args, calls manifest resolution + tools engine
3. `platform_cli/manifests/*`
   - Loads + validates + resolves team->tool list
4. `platform_cli/tools/service.py`
   - Detects tool state
   - Applies install/upgrade/uninstall logic
   - Updates persistent state in `platform_cli/state/store.py`
   - Runs OS package manager commands via `platform_cli/exec/runner.py`
5. Errors bubble up to `cli.py::_run()` and are printed consistently.

---

## Folder-by-folder guide

### `src/platform_cli/commands/`
**Purpose:** CLI command definitions only (Typer wiring + UX).

**What belongs here**
- Typer command registration functions
- Argument parsing
- Calling services (tools engine, manifests, etc.)
- Lightweight orchestration

**What should NOT live here**
- OS-specific install logic
- `subprocess` calls
- manifest schema validation logic
- persistence logic

> Rule of thumb: command modules should stay small and read like “controller code”.

---

### `src/platform_cli/core/`
**Purpose:** cross-cutting runtime behaviors used by many commands.

Examples:
- `context.py` — runtime flags: `verbose`, `quiet`, `json`, `non_interactive`
- `errors.py` — `PlatformError` (domain error)
- `output.py` — shared printing helpers (headers, welcome, formatted errors)
- `telemetry.py` — error logging + alerts
- `update.py` — update check logic / throttling

**Why it exists:** commands should not each reinvent error/output/context patterns.

---

### `src/platform_cli/manifests/`
**Purpose:** “data model + validation + resolution” for manifests.

- `load.py` — loads manifest JSON and selects the platform key
- `validate.py` — validates and resolves team->tool list

**Why it is NOT in `tools/`**
Manifests answer: **“what should be installed?”**
Tools engine answers: **“how do we install it on this machine?”**

Keeping them separate prevents the tools engine from mixing:
- config schema validation AND
- runtime OS operations

---

### `src/platform_cli/tools/`
**Purpose:** reusable, domain-specific helper logic for tool setup workflows.

Examples:
- `service.py` — orchestration engine for detect/install/upgrade/uninstall
- `versions.py` — version requirement parsing/validation
- `winget.py` — Windows winget helper/repair
- `aws_sso.py` — AWS SSO bootstrap/wizard logic

**Why tools logic is spread across multiple folders**
- `commands/` = UX + routing
- `manifests/` = desired state resolution
- `tools/` = actual state enforcement
- `exec/` = process execution
- `state/` = persistence

This avoids one giant “commands/tools.py” file that becomes impossible to test and reason about.

---

### `src/platform_cli/exec/`
**Purpose:** one consistent subprocess runner.

Why:
- consistent stdout/stderr capture behavior
- consistent error mapping (`E_CMD_FAILED`, etc.)
- centralized place to add logging/diagnostics later

---

### `src/platform_cli/state/`
**Purpose:** persistence for GHDP-specific state, not system state.

Examples stored:
- detected versions
- last install/upgrade/uninstall actions
- “managed_by” flags
- timestamps (e.g., update checks)

**Important:** state is intentionally persistent across reinstalls unless user deletes it.

---

### `src/platform_cli/resources/`
**Purpose:** bundled configuration shipped with the CLI.

Includes:
- tool registry (tools + per-OS commands)
- toolsets (teams -> tools + version requirements)

**Rule:** if you can express something as data, prefer manifests over hardcoding logic.

---

## Error handling design (current)

### Domain error: `PlatformError`
`PlatformError` is the domain-level exception used across the CLI.
It carries:
- `message` (human readable)
- optional `code` (stable error code for scripts)
- optional `reason` (subsystem key: `winget`, `aws_sso`, `manifest`, etc.)
- `alert` flag (telemetry escalation)

### Where errors are raised vs caught
- Raise `PlatformError` **at the layer that knows the meaning** of the failure.
  - tools engine: version policy, install flow, user-managed decisions
  - manifests: invalid team/tool references, invalid config
- Let errors bubble to `cli.py::_run()` where printing is centralized.

### Why you sometimes see `except PlatformError: raise`
Intentional:
- preserve the original `PlatformError` (already has the right code/reason)
- convert unknown exceptions into a `PlatformError` with a stable code

**Rule**
- `except PlatformError: raise`
- then `except Exception as e: raise PlatformError(...)`

Do NOT swallow errors silently unless it is an explicitly best-effort feature (e.g., update hints).

---

## SOLID principles (lightweight)

We use SOLID as guidance, **not dogma**.

### Where SOLID is applied
- **SRP:** commands are routing/UX; tools engine owns install logic.
- **OCP:** adding a new tool is usually manifest-only.
- **ISP:** helpers split by concern (`aws_sso`, `winget`, `versions`).

### Intentional deviations (do NOT “fix” these casually)

1. **Tool-specific post-hooks (OCP deviation)**
   - Example: AWSCLI triggers AWS SSO bootstrap.
   - Intentional Guardant-specific onboarding/security requirement.

2. **A single orchestration engine (`tools/service.py`) (SRP deviation)**
   - Centralizes install/upgrade/uninstall decisions to keep behavior consistent.

3. **Global CLI context (`core/context.py`) (DIP deviation)**
   - Uses a global context so all layers respect `--non-interactive`, `--quiet`, etc.
   - Avoids threading context through every function signature.

4. **Compatibility shims**
   - Some modules may defensively import `PlatformError`.
   - Until unified, avoid introducing new duplicate error classes.

Refactors here must be done as dedicated “architecture refactor” PRs, not drive-by cleanup.

---

## Rules for contributors (humans + coding agents)

### Adding a new CLI command
- Create under `platform_cli/commands/`
- Export `register(app)`
- Keep it thin; put real logic under `core/` or `tools/`

### Adding a new tool
- Prefer editing manifests in `resources/`
- Only add code in `tools/` if special behavior is truly required

### Adding persistent state
- Add keys via `state/store.py` usage
- Keep state backward compatible

### Running subprocesses
- Always use `exec/runner.py::run_cmd`
- Do not call `subprocess.run` directly in commands/tools

---

## Make coding agents read this first (repo standard)

Add these files pointing here:
- `README.md` section “Contributing”
- `.github/copilot-instructions.md`
- `CLAUDE.md`
- `.cursorrules`
- `AGENTS.md`

Each must instruct:
> Read `ARCHITECTURE.md` first. Do not reorganize layers unless explicitly requested.

---
