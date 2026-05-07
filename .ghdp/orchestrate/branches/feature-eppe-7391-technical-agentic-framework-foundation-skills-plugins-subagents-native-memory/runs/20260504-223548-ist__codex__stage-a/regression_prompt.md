# Stage 14 Regression Prompt

- Agent: `regression-validation`
- Role: `behavior_guard`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Ticket: `EPPE-7391`

## Prompt Contract
- Protect already-working behavior before expanding into new implementation work.
- Be explicit about what is covered and what still needs new tests.

## Allowed Skills
- `touched-scope-regression`

## Allowed Plugins
- `provider-codex`
- `provider-claude`

## Regression Selection Posture
- Protect already-working behavior before expanding into new implementation work.
- Prefer the narrowest relevant tests first, then add shared-capability tests when runtime, manifests, and contracts intersect.
- Make the selected regression surface explicit enough that Stage 16 can execute it without rediscovering scope.

