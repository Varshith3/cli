# Orchestrate Stages

This folder contains repo-level stage recipes for the Phase 1 orchestrator.

Each stage contract is declarative and repo-owned. The CLI engine reads these
files to shape:

- stage prompt scaffolding
- handoff wording
- next-action guidance
- resume-note templates
- stage-specific operator/reviewer posture

The CLI code still owns:

- safety checks
- stage ordering
- state mutation
- git operations
- output rendering
- error handling

Implemented stage contracts currently include:

- `stage_c_front_door_gates`
- `stage_d_review_layer`
- `stage_e_execution_prep`
- `stage11_implementation`
- `stage12_commit_push`
- `stage13_qa_scenario_design`
- `stage14_touched_scope_regression`
- `stage15_new_test_coverage`
- `stage16_developer_test_execution`
- `stage17_packaged_artifact_validation`
- `stage18_release_readiness`
- `stage19_prerelease_creation`
- `stage20_release_notes_refresh`
- `stage21_pr_external_integration`
- `stage22_traceability_capture`
