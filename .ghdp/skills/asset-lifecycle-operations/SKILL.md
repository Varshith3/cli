# Asset Lifecycle Operations

Purpose:
- create, revise, version-update, or retire GHDP-managed capability assets through a lightweight, explicit asset lifecycle path

When to use:
- when a request is only about changing existing asset content or version metadata
- when the orchestrator determines full SDLC is not required
- during SDLC when capability assets must change as one part of a larger enhancement or bug fix

Prompt contract:
- choose one operation: `create`, `revise`, `update_versioned_asset`, or `remove`
- explicitly list touched files before mutation
- include manifest/index/release implications for release-backed assets
- include allowlist/source-branch implications for marketplace-backed assets
- keep the path lightweight when only asset content is changing
- escalate into full SDLC only when code, behavior, or release flow changes go beyond asset management

Expected outputs:
- `asset_operation_plan`
- `asset_operation_result`
- `asset_release_implications`
