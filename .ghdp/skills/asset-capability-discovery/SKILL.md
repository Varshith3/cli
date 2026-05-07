# Asset Capability Discovery

Purpose:
- understand existing GHDP capability assets before deciding whether to run full SDLC or a lightweight asset-only path

When to use:
- when the request is about revising an existing capability asset
- when the request is about sync-managed content, manifests, toolset payloads, allowlists, policy assets, or marketplace-backed skills/plugins
- before creating, versioning, or removing a capability asset

Prompt contract:
- identify whether the target is release-backed or marketplace-backed
- identify the live capability id when one already exists
- identify the source-of-truth files that define the asset today
- state what must change for create, revise, version update, or removal
- separate “asset-only operation” from “full SDLC change”

Expected outputs:
- `asset_inventory`
- `asset_target_candidates`
- `asset_update_scope`
