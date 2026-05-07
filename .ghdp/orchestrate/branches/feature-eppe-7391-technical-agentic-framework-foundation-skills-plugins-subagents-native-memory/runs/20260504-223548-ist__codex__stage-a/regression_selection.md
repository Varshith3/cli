# Regression Selection

- Status: `selected`
- Owner agent: `regression-validation`
- Selected test count: `6`

## Selected Tests
- `platform-cli/tests/test_orchestrate_contract.py`
- `platform-cli/tests/test_orchestrate_runtime.py`
- `platform-cli/tests/test_orchestrate_front_door.py`
- `platform-cli/tests/test_orchestrate_review.py`
- `platform-cli/tests/test_orchestrate_manifests.py`
- `platform-cli/tests/test_orchestrate_qa.py`

## Selection Reasons
- `platform-cli/tests/test_orchestrate_contract.py` protects repo-owned contract validation and stage-registry integrity.
- `platform-cli/tests/test_orchestrate_runtime.py` protects lifecycle bootstrap, pause/resume, and branch-state mutation behavior.
- `platform-cli/tests/test_orchestrate_front_door.py` keeps the intake/classification path stable before later execution stages build on it.
- `platform-cli/tests/test_orchestrate_review.py` protects the architecture and UX/DX review loop that gates execution.
- `platform-cli/tests/test_orchestrate_manifests.py` covers `.ghdp` contract validation because the touched surface includes repo-owned orchestrator artifacts.
- `platform-cli/tests/test_orchestrate_qa.py` keeps the Stage 13 QA packet aligned with the scenarios that Stage 14 inherits.
- Stage 13 is now upstream of regression selection, so its packet must remain stable when later stages consume it.
- The QA plan explicitly includes failure-path coverage, so regression selection preserves the narrow tests that surface those failures early.

