# Touched Scope Regression

Purpose:
- protect already-working behavior in areas touched directly or indirectly by the branch.

When to use:
- after implementation plan exists
- before broad execution claims are made

Prompt contract:
- use impacted areas from `poa.md`
- prefer the narrowest relevant tests first
- include shared-capability tests when the touched surface crosses command/runtime/manifests

Expected outputs:
- `regression_plan.md`
- selected tests list
- reason each test is included

