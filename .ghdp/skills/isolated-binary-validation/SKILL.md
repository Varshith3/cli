# Isolated Binary Validation

Purpose:
- validate the packaged binary in an isolated install-and-run flow instead of stopping at source-mode checks.

When to use:
- before advancing a change into final release-facing stages

Prompt contract:
- install the packaged artifact cleanly
- run the intended user-facing command path
- record exact observed behavior and any packaging gaps

Expected outputs:
- `artifact_validation_result`
