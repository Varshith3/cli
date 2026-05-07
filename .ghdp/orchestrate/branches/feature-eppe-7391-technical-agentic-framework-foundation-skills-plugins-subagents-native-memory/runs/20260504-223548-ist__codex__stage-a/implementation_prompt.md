# Stage 11 Implementation Prompt

- Agent: `implementation`
- Role: `delivery_worker`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Ticket: `EPPE-7391`

## Prompt Contract
- Apply the approved plan without drifting from the reviewed scope.
- Keep branch artifacts current as the code changes evolve.

## Allowed Skills
- `traceability-and-resume`

## Allowed Plugins
- `provider-codex`
- `provider-claude`
- `native-memory-filesystem`

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
- context-capability-discovery
- folder-backed-shared-memory
- native-memory-filesystem
- orchestrator
- repo-local-code-context
- provider-claude
- provider-codex
- regression-validation
- parallel-work-awareness
- qa-scenario-design
- qa-scenario-generation
- architecture-review

## Expected Delivery Posture
- Apply the approved plan without drifting from the reviewed scope.
- Keep repo-backed branch artifacts current as code and docs evolve.
- Be ready to hand off cleanly into commit/push and validation stages.

