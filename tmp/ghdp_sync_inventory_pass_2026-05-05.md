# GHDP Sync Full Inventory Pass

Date: 2026-05-05
Repo: `C:\Users\Hi\Downloads\git-repos\dp-tools-local-setup`
Branch: `feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory`

## Scope

This note captures a full code-grounded inventory pass of the current GHDP sync system:

- how sync is modeled
- which capability providers exist
- what capabilities exist today
- what each capability is for
- what actually changes when a capability is revised or versioned forward
- where the current behavioral gaps are

## Core Model

The sync engine is centered on:

- `platform-cli/src/platform_cli/core/release_content.py`
- `platform-cli/src/platform_cli/core/sync_providers.py`
- `platform-cli/src/platform_cli/core/sync_targets.py`
- `platform-cli/src/platform_cli/commands/sync.py`

The live index source defaults are:

- repo: `gh-org-data-platform/dp-tools-local-setup`
- tag: `content-index-latest`
- asset: `content-index.json`

The default manifest asset name is:

- `content-manifest.json`

The sync flow is:

1. Load the release-backed `content-index.json`
2. Validate raw release-backed capability entries
3. Load managed allowlist policy
4. Expand marketplace-backed capabilities from policy into generated capability entries
5. Optionally filter the resulting capability set through team policy
6. Scan local install state
7. Compute action:
   - `install`
   - `repair`
   - `update`
   - `blocked`
   - `none`

## Provider Families

There are two real sync provider families today.

### 1. GitHub release asset provider

Provider id:

- `github_release`

Implementation:

- `GitHubReleaseProvider` in `platform-cli/src/platform_cli/core/sync_providers.py`

Behavior:

- resolves version from release tag
- downloads assets from a GitHub release
- downloads `content-manifest.json`
- treats the manifest as the install contract

Typical use:

- packaged managed capabilities
- policy assets
- repo-ready assets
- scheduler assets
- Tableau jars
- Claude/Codex AWS skill bundles

### 2. Marketplace repo provider

Provider id:

- `marketplace_repo`

Implementation:

- `MarketplaceRepoProvider` in `platform-cli/src/platform_cli/core/sync_providers.py`

Behavior:

- resolves version to a commit SHA
- snapshots a repo at a branch/commit
- installs from repo content rather than release assets
- supports:
  - `skill`
  - `plugin`
- rewrites plugin manifest folder names as needed between Codex/Claude plugin layouts

Typical use:

- marketplace skills
- marketplace plugins

Default marketplace repo:

- `gh-org-data-platform/gh-dp-data-platform-skill-marketplace`

Default marketplace branch:

- `develop`

## Target Types

Current target handlers registered in `sync_targets.py`:

- `filesystem`
- `codex_skills`
- `codex_plugins`
- `claude_skills`
- `claude_plugins`
- `tableau_drivers`

All current handlers resolve to a filesystem root plus a target subdirectory.

## Live Capability Inventory Today

### Raw release-backed capabilities from `content-index.json`

The current live raw index contains 9 release-backed capabilities:

1. `codex-skills-aws`
2. `claude-skills-aws`
3. `tableau-athena-jars`
4. `marketplace-skill-allowlist`
5. `repo-ready-assets`
6. `ghdp-team-toolset`
7. `claude-athena-workgroup-map`
8. `background-scheduler`
9. `ghdp-admin-policy`

### Generated marketplace-backed capabilities

The current policy expansion generates 8 marketplace-backed capabilities:

10. `marketplace-claude-skill-skill-workbench`
11. `marketplace-claude-skill-git-branch-review`
12. `marketplace-claude-plugin-query-athena`
13. `marketplace-claude-plugin-data-governance`
14. `marketplace-codex-skill-skill-workbench`
15. `marketplace-codex-skill-git-branch-review`
16. `marketplace-codex-plugin-query-athena`
17. `marketplace-codex-plugin-data-governance`

Total visible capability count today:

- `17`

## Capability-by-Capability Purpose

### Release-backed capabilities

#### `codex-skills-aws`

Purpose:

- installs the AWS read-only runbook skill into `~/.codex/skills/aws-readonly-runbook`

Code path:

- `platform-cli/src/platform_cli/tools/codex_skill_sync.py`

Current live tag:

- `codex-skills-aws-v1.0.0`

Current local state in this environment:

- installed

#### `claude-skills-aws`

Purpose:

- installs the AWS read-only runbook skill into `~/.claude/skills/aws-readonly-runbook`

Code path:

- `platform-cli/src/platform_cli/tools/claude_skill_sync.py`

Current live tag:

- `claude-skills-aws-v1.0.0`

Current local state in this environment:

- not installed

#### `tableau-athena-jars`

Purpose:

- installs Tableau Athena driver jars into the Tableau driver location

Primary related config:

- `platform-cli/src/platform_cli/resources/tableau-athena-init.json`

Current live tag:

- `v0.1.0-tableau-athena-jars`

Current local state in this environment:

- installed

#### `marketplace-skill-allowlist`

Purpose:

- provides the managed sync allowlist policy payload used to decide which marketplace capabilities exist at all

Important payload file:

- `capability-allowlist.managed.json`

Code path:

- `_load_managed_allowlist_policy()` in `release_content.py`

Current live tag:

- `marketplace-skill-allowlist-v1.2.3`

Current local state in this environment:

- not installed

#### `repo-ready-assets`

Purpose:

- provides packaged repo-readiness prompts, templates, GHDP seed files, workflow template(s), and vocabulary

Code path:

- `platform-cli/src/platform_cli/tools/repo_ready_assets.py`

Install root:

- `~/.ghdp/repo_ready/base`

Current live tag:

- `repo-ready-assets-v1.0.0`

Current local state in this environment:

- not installed

#### `ghdp-team-toolset`

Purpose:

- provides the managed team-toolset payload used by GHDP tooling/tool-install policy flows

Likely managed file:

- `~/.ghdp/policies/team-toolset.managed.json`

Code path:

- `platform-cli/src/platform_cli/tools/team_toolset_assets.py`

Current live tag:

- `ghdp-team-toolset-v1.0.5`

Current local state in this environment:

- installed

#### `claude-athena-workgroup-map`

Purpose:

- provides a managed Claude Athena workgroup mapping file

Likely managed file:

- `~/.ghdp/policies/claude-athena-workgroup-map.managed.json`

Code path:

- `platform-cli/src/platform_cli/tools/claude_athena_workgroup_assets.py`

Current live tag:

- `claude-athena-workgroup-map-v1.0.3`

Current local state in this environment:

- not installed

#### `background-scheduler`

Purpose:

- provides the managed scheduler capability payloads used for scheduler reconciliation and local scheduler task materialization

Code paths:

- `platform-cli/src/platform_cli/tools/scheduler_assets.py`
- `platform-cli/src/platform_cli/manifests/scheduler.py`

Install root:

- `~/.ghdp/capabilities/scheduler`

Special behavior:

- has packaged fallback/bootstrap assets under `platform-cli/src/platform_cli/resources/scheduler/bootstrap`

Current live tag:

- `background-scheduler-v1.0.6`

Current local state in this environment:

- not installed

#### `ghdp-admin-policy`

Purpose:

- provides synced admin/support policy content used by GHDP access and support guidance paths

Observed important behavior from repo docs:

- this capability currently carries `sync.mutate`
- it is intentionally part of bootstrap/recovery behavior so sync can self-heal even after local GHDP state loss

Current live tag:

- `ghdp-admin-policy-v1.1.8`

Current local state in this environment:

- not installed

Only this live capability currently advertises:

- `allow_new_files_on_update = true`

### Marketplace-backed capabilities

These are generated from the allowlist policy and installed from the marketplace repo snapshot, not from GitHub release assets.

#### `marketplace-claude-skill-skill-workbench`

Purpose:

- installs the `skill-workbench` skill into Claude skills

Install root:

- `~/.claude/skills/skill-workbench`

Current local state:

- not installed

#### `marketplace-claude-skill-git-branch-review`

Purpose:

- installs the `git-branch-review` skill into Claude skills

Install root:

- `~/.claude/skills/git-branch-review`

Current local state:

- not installed

#### `marketplace-claude-plugin-query-athena`

Purpose:

- installs the `query-athena` plugin into Claude plugins

Install root:

- `~/.claude/plugins/query-athena`

Current local state:

- not installed

#### `marketplace-claude-plugin-data-governance`

Purpose:

- installs the `data-governance` plugin into Claude plugins

Install root:

- `~/.claude/plugins/data-governance`

Current local state:

- not installed

#### `marketplace-codex-skill-skill-workbench`

Purpose:

- installs the `skill-workbench` skill into Codex skills

Install root:

- `~/.codex/skills/skill-workbench`

Current local state:

- installed

#### `marketplace-codex-skill-git-branch-review`

Purpose:

- installs the `git-branch-review` skill into Codex skills

Install root:

- `~/.codex/skills/git-branch-review`

Current local state:

- installed

#### `marketplace-codex-plugin-query-athena`

Purpose:

- installs the `query-athena` plugin into Codex plugins

Install root:

- `~/.codex/plugins/query-athena`

Current local state:

- installed

#### `marketplace-codex-plugin-data-governance`

Purpose:

- installs the `data-governance` plugin into Codex plugins

Install root:

- `~/.codex/plugins/data-governance`

Current local state:

- installed

## Current Live Status Snapshot In This Environment

Installed today:

- `codex-skills-aws`
- `tableau-athena-jars`
- `ghdp-team-toolset`
- `marketplace-codex-skill-skill-workbench`
- `marketplace-codex-skill-git-branch-review`
- `marketplace-codex-plugin-query-athena`
- `marketplace-codex-plugin-data-governance`

Not installed today:

- `claude-skills-aws`
- `marketplace-skill-allowlist`
- `repo-ready-assets`
- `claude-athena-workgroup-map`
- `background-scheduler`
- `ghdp-admin-policy`
- all 4 Claude marketplace capabilities

## What Changes When A Capability Is Updated

This depends on provider family.

### A. Release-backed capability update

For a release-backed capability, the full publish/update surface is:

1. Update the actual file payloads that should ship
2. Regenerate or edit the capability’s `content-manifest.json`
3. Ensure the manifest declares:
   - `capability`
   - `version`
   - `target_root_key`
   - `target_subdir`
   - `files[]` with `asset_name` and `target_path`
4. Publish a GitHub release/tag containing:
   - all payload assets
   - the matching `content-manifest.json`
5. Update `content-index.json` entry fields if this should become the active version:
   - `version`
   - `tag`
   - `manifest_asset` when the asset name changes
   - policy flags if rollout behavior changes
6. Refresh `content-index-latest` so GHDP sees the new entry

If the capability is already installed locally, update behavior is then driven by:

- tracked file list from local state
- latest manifest file list
- policy flags

### B. Marketplace-backed capability update

For a marketplace-backed capability, the full publish/update surface is:

1. Update the marketplace repo contents
2. The effective version changes when the source branch resolves to a new commit SHA
3. If the allowlist policy changes:
   - skill list changes
   - plugin list changes
   - explicit entry list changes
   then the generated capability inventory changes too
4. GHDP resolves the new commit and compares against installed state

So for marketplace-backed capabilities, the “version” is effectively:

- the resolved repo commit SHA

and the inventory can also change when:

- the managed allowlist policy changes

## What The Local State Tracks

Per capability, GHDP state records things like:

- capability id
- provider
- provider source
- package type
- target type
- category
- policy
- install path
- tracked files
- detected extra local files
- repo/tag/version/manifest asset when recorded
- last verified timestamp
- content hash

This is why update/repair/install behavior is driven by tracked state, not just by the latest remote manifest.

## Actual Update Semantics Today

The current implementation behavior is important:

- `install` is allowed only when `allow_install_if_missing = true`
- `repair` restores missing tracked files
- `update` only updates already-tracked files

The key implementation detail:

- update selection is based on `tracked_files`
- `apply_content_update()` writes only `updatable_files`
- `updatable_files` are chosen only from files already in `tracked_files`

That means:

- new manifest files are not automatically added to an existing install during update

The repo exposes metadata for:

- `allow_new_files_on_update`

but the current operational path still behaves as:

- “update tracked files only”

This is a real gap, not just a theoretical one.

Observed evidence:

- `allow_new_files_on_update` is present in index/state payloads
- but the update path still only writes selected tracked files
- `ignored_new_files` is computed for status reporting, not materialized during update

## Important Subtlety: Team Policy Capability

There is code support for a managed team policy capability name:

- `ghdp-team-policy`

but it is not present in the current raw live `content-index.json` payload that was fetched in this pass.

What exists today instead:

- optional team policy loader precedence in `platform_cli/manifests/load.py`
- packaged fallback support
- synced file path conventions

So the current repo definitely understands managed team policy as a concept, but the currently visible live index inventory in this pass did not expose `ghdp-team-policy` as one of the 9 raw release-backed capabilities.

## Practical Rules For Publishing A Capability Revision

### If the provider is `github_release`

You must think about all of:

- payload file changes
- manifest file changes
- release tag/version
- content index entry
- install/update policy flags

### If the provider is `marketplace_repo`

You must think about all of:

- marketplace repo contents
- source branch/commit movement
- allowlist policy entries
- target mapping:
  - Codex vs Claude
  - skill vs plugin

## Short Operational Summary

If someone says “update a synced capability,” the safe interpretation today is:

### Release-backed

- update payload files
- update manifest
- publish new release tag
- update `content-index.json`
- move `content-index-latest`

### Marketplace-backed

- update marketplace repo contents
- ensure allowlist policy still exposes the capability
- let the commit SHA become the new effective version

## Inventory Conclusion

After this pass, the sync model is now clear and bounded:

- live visible capability count today: `17`
- raw release-backed capabilities: `9`
- generated marketplace-backed capabilities: `8`
- provider families: `2`
- target families: `6`

The biggest real behavior caveat I found is:

- `allow_new_files_on_update` is modeled, but update is still effectively “tracked files only”

That is the main place where the current system behavior is narrower than the metadata suggests.
