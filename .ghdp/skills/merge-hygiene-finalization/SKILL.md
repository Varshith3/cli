# Merge Hygiene Finalization

Use this skill when a feature branch is ready to stop carrying runtime-only orchestrator artifacts into `develop`.

What this skill must do:
- confirm Stage 22 traceability capture has already completed
- archive the branch runtime folder for short-term safety retention
- promote only the durable closeout summary into `.ghdp/memory/shared/`
- prune `.ghdp/orchestrate/branches/<branch>/...` from the working tree
- leave a machine-readable receipt so local runs and CI can verify merge readiness

What this skill must not do:
- keep runtime-only branch folders in merge-bound commits
- overwrite durable shared memory with noisy stage-by-stage replay data
- hide missing closeout evidence behind a soft warning

Expected outputs:
- a promoted shared-memory closeout summary
- a closeout receipt confirming archive retention and runtime pruning
- a merge-safe branch state that `ghdp orchestrate verify-merge-hygiene` can validate
