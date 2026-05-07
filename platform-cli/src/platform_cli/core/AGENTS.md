# Agent Notes (core)

This folder contains cross-cutting runtime capabilities used across the CLI.

Examples:
- context flags (`cli_ctx`)
- `PlatformError` (single exception type)
- printing/output helpers
- telemetry and alerting hooks
- update check behavior

## Rules
- No command wiring here (no Typer commands).
- Keep APIs stable; many layers depend on these utilities.
- Use `PlatformError(message, code=..., reason=...)` for domain failures.
