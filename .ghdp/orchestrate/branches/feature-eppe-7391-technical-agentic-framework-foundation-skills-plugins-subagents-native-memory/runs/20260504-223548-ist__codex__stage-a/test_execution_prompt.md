# Stage 16 Developer Test Execution Prompt

- Agent: `developer-test-execution`
- Role: `validation_executor`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Ticket: `EPPE-7391`

## Prompt Contract
- Execute the planned validation in a reproducible order.
- Respect shared local resources and lock-sensitive flows while testing.

## Allowed Skills
- `developer-test-execution`

## Allowed Plugins
- `native-memory-filesystem`

## Execution Posture
- Run the focused regression and authored coverage backlog in a deterministic order.
- Serialize this stage when local runtime artifacts or locks could interfere with one another.
- Capture the exact command and output so a later owner can replay the same validation path.

- Execution mode: `sequential`

