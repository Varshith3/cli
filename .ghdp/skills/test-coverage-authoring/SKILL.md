# Test Coverage Authoring

Purpose:
- add or update tests for new or changed behavior introduced by the branch.

When to use:
- after regression targets are known
- before developer test execution is finalized

Prompt contract:
- encode changed behavior, not only happy paths
- cover command output, runtime artifact mutation, and failure handling where relevant
- keep tests small and aligned with repo architecture

Expected outputs:
- `coverage_plan.md`
- new or updated test files
- list of missing coverage risks

