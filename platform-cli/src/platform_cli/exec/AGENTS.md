# Agent Notes (exec)

Single source of truth for running subprocesses.

## Rule
Do not call `subprocess.run` directly in other layers.
Use `run_cmd()` from this folder so errors are normalized into `PlatformError`.
