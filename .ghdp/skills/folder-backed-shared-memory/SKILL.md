# Folder Backed Shared Memory

Purpose:
- provide interim shared-memory behavior using repo and user-global folders.

When to use:
- when overlap detection, traceability, or resume behavior needs shared state

Prompt contract:
- use repo-backed artifacts for shareable state
- use user-global folders only for machine-local runtime details

Expected outputs:
- `shared_memory_records`
