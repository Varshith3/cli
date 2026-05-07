# Implementation Plan

- Work type: `new_feature`
- Intent: convert the accepted orchestrator plan into concrete code changes without breaking the earlier runtime slices.

## Primary Targets
- `.ghdp/agents/manifest.json`
- `.ghdp/memory/context/README.md`
- `.ghdp/memory/manifest.json`
- `.ghdp/memory/shared/README.md`
- `.ghdp/plugins/manifest.json`
- `.ghdp/skills/manifest.json`
- `platform-cli/src/platform_cli/commands/orchestrate.py`
- `platform-cli/src/platform_cli/tools/orchestrate_front_door.py`
- `platform-cli/src/platform_cli/tools/orchestrate_runtime.py`

## Capability Reuse Notes
- `context-capability-discovery`
- `folder-backed-shared-memory`
- `native-memory-filesystem`
- `orchestrator`
- `repo-local-code-context`
- `provider-claude`
- `provider-codex`
- `regression-validation`
- `parallel-work-awareness`
- `qa-scenario-design`
- `qa-scenario-generation`
- `architecture-review`

## Execution Posture
- Keep commands thin and put runtime logic under `tools/`.
- Keep manifests and policy loading under `manifests/`.
- Update repo-local runtime artifacts together with code changes when the stage meaning evolves.

