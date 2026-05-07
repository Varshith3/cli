# Phase 1 Plugin Inventory

This file is the human-readable companion to `.ghdp/plugins/manifest.json`.

In Phase 1, plugins are adapter bundles. They should stay thin and environment-focused. Business logic belongs in the orchestrator runtime or skills, not inside plugin wrappers.

Execution-ready plugin payloads now begin to live under:
- `.ghdp/plugins/<plugin-id>/plugin.json`

The first concrete payload set was added for the Stage E execution layer so provider, Jira, Jenkins, GitHub release, sync, and native-memory setup/login contracts are repo-visible.

## Plugins

### `provider-codex`
Codex-facing provider bridge. It exists so Codex can consume repo-level agents/skills/plugins from `.ghdp` consistently.

### `provider-claude`
Claude-facing provider bridge. It exists so Claude can consume the same `.ghdp` contracts without parallel drift.

### `provider-vscode-codex`
VS Code Codex host-facing provider bridge. It exists so headless and editor-hosted Codex runs can resolve the same `.ghdp` contracts and parallel/sequential topology.

### `provider-vscode-claude`
VS Code Claude host-facing provider bridge. It exists so headless and editor-hosted Claude runs can resolve the same `.ghdp` contracts and parallel/sequential topology.

### `jira-acli`
Owns Jira interaction through ACLI.

### `jenkins-mcp`
Owns Jenkins-driven release and PR integration behavior.

### `github-release-gh`
Owns GitHub release operations used by prerelease and packaged artifact flows.

### `github-pr-gh`
Owns portable GitHub pull-request creation and PR comment progression through `gh`.

### `sync-minimal`
Provides the minimum GHDP sync-awareness needed in Phase 1.

### `asset-lifecycle-sync`
Provides the lightweight capability-asset inventory and mutation contract so asset-only work can be handled independently or inside a larger SDLC run.

### `native-memory-filesystem`
Provides the interim repo-local and user-global memory/storage behavior frozen for Phase 1.
