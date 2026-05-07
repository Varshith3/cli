# Jira ACLI Integration

Purpose:
- handle Jira updates through ACLI with explicit, reproducible behavior.

When to use:
- when the orchestrator needs to comment or later update Jira state

Prompt contract:
- prefer ACLI over ad hoc REST or browser-only behavior
- keep updates concise and traceable to branch/prerelease/PR state
- fail clearly when ACLI auth is unavailable

Expected outputs:
- Jira update summary
- exact ACLI action taken

