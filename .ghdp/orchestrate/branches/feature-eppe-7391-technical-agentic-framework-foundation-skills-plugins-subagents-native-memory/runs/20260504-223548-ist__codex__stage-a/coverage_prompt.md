# Stage 15 Coverage Prompt

- Agent: `test-coverage-authoring`
- Role: `coverage_expander`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Ticket: `EPPE-7391`

## Prompt Contract
- Add tests where behavior changed instead of broad test churn.
- Keep coverage additions understandable and proportional to the change.

## Allowed Skills
- `test-coverage-authoring`

## Allowed Plugins
- `provider-codex`
- `provider-claude`

## Coverage Authoring Posture
- Add only the smallest new or expanded tests needed to protect the current changed behavior.
- Prefer repo-owned orchestrator tests over broad churn outside the touched surface.
- Keep the backlog explicit enough that developer test execution can run it without rediscovering intent.

