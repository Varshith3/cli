# Stage 19 Prerelease Prompt

- Agent: `release-prerelease`
- Role: `release_operator`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Ticket: `EPPE-7391`

## Prompt Contract
- Create prerelease outputs only after readiness is explicit.
- Keep prerelease behavior consistent whether it is run locally or from CI.

## Allowed Skills
- `release-and-pr`
- `stable-release-notes-assembly`

## Allowed Plugins
- `github-release-gh`
- `github-pr-gh`
- `jenkins-mcp`

## Prerelease Posture
- Use the existing release engine instead of inventing a second prerelease path.
- If the release engine blocks, record the exact blocker rather than hiding it behind a generic failure.
- Keep the prerelease packet small and factual so later stages can communicate it cleanly.

