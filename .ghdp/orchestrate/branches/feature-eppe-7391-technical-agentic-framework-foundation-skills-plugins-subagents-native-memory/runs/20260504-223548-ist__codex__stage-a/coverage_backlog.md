# Coverage Backlog

- Status: `authored`
- Owner agent: `test-coverage-authoring`
- Authored test count: `5`

## New or Expanded Tests
- `platform-cli/tests/test_orchestrate_execution.py`
- `platform-cli/tests/test_orchestrate_coverage.py`
- `platform-cli/tests/test_orchestrate_regression.py`
- `platform-cli/tests/test_orchestrate_contract.py`
- `platform-cli/tests/test_orchestrate_manifests.py`

## Coverage Rationale
- `platform-cli/tests/test_orchestrate_execution.py` protects execution-prep bindings that downstream stages still depend on.
- `platform-cli/tests/test_orchestrate_coverage.py` proves the new coverage-authoring stage stays repo-driven and resumable.
- `platform-cli/tests/test_orchestrate_regression.py` keeps Stage 14 and Stage 15 aligned so coverage follows the same touched scope the regression set established.
- `platform-cli/tests/test_orchestrate_contract.py` protects the visible orchestrate command surface because new coverage work still relies on stable command wiring.
- `platform-cli/tests/test_orchestrate_manifests.py` keeps the `.ghdp` contract validation path healthy because the coverage stage consumes repo-owned recipes and agent contracts.
- The first coverage goal remains the anchor for this backlog: `Add or extend tests for any new Stage E command surface and runtime artifact writer.`.
- The QA scenario plan still includes failure-path behavior, so at least one new coverage item must preserve that branch of execution.

