# Stage 13 QA Scenario Prompt

- Agent: `qa-scenario-design`
- Role: `scenario_designer`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Ticket: `EPPE-7391`

## Prompt Contract
- Design realistic validation scenarios tied back to acceptance, not generic smoke checks.
- Favor edge cases and operator-facing failure paths when they matter.

## Allowed Skills
- `qa-scenario-generation`

## Allowed Plugins
- `provider-codex`
- `provider-claude`

## Acceptance Anchors
- skills, plugins, and sub-agent orchestration are usable through a consistent framework path
- native memory handling works for the baseline agentic flow without external memory dependencies
- framework docs clearly separate current baseline capabilities from later EPPE-7581 integration work

## Scenario Design Posture
- Tie every scenario back to the acceptance and touched scope of this branch run.
- Include operator-facing failures and recovery paths, not just the success path.
- Keep the output ready for Stage 14 and Stage 16 to consume without re-deriving context.

