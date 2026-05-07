# Agent Notes (tools)

Tools layer answers: **how to make the machine match desired state**.

This folder contains:
- tool detection/install/upgrade/uninstall orchestration
- OS/manager specific helpers (winget, brew, aws sso, version checks)

## Rules
- Always run processes via `exec/runner.py::run_cmd`.
- Raise `PlatformError` with stable `code` and `reason` when possible.
- Avoid pulling CLI argument parsing into this layer.
- Keep tool behavior consistent across commands (shared engine).
