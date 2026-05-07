# Stage 17 Packaged Artifact Validation Prompt

- Agent: `binary-validation`
- Role: `artifact_validator`
- Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`
- Ticket: `EPPE-7391`

## Prompt Contract
- Validate the packaged artifact in isolation, not only source-mode behavior.
- Record the exact install-and-run evidence needed for release confidence.

## Allowed Skills
- `isolated-binary-validation`

## Allowed Plugins
- `native-memory-filesystem`

## Validation Posture
- Validate the packaged CLI path in isolation instead of relying only on source-mode commands.
- Serialize pipx operations so other sessions cannot corrupt the validation install.
- Record the exact smoke commands and observed outputs for later release confidence.

