# Asset Operation Plan

- Operation: `update_versioned_asset`
- Asset target: `toolset_codex_version`
- Agent: `asset-lifecycle`

## Allowed Skills
- `asset-capability-discovery`
- `asset-lifecycle-operations`
- `minimal-sync-decision`
- `traceability-and-resume`

## Allowed Plugins
- `asset-lifecycle-sync`
- `sync-minimal`
- `native-memory-filesystem`
- `provider-codex`
- `provider-claude`

## Known Target Contract
- `Inventory the target capability before mutating any asset files.`
- `Treat release-backed assets as manifest/index/tag aware, even when the current repo edit only changes local source files.`
- `Treat marketplace-backed assets as allowlist and repo-snapshot aware.`
- `Prefer the lightweight asset lifecycle path when the user is revising existing asset content without broader code behavior changes.`

## Planned File Touches
- `platform-cli/src/platform_cli/resources/manifests/toolset.json`
- `platform-cli/release-assets/team_toolset/toolset.json`
