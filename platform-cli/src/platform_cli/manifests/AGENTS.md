# Agent Notes (manifests)

Manifests answer: **what should be installed**.

This folder is for:
- loading JSON manifests
- validating manifest schema/refs
- resolving team -> tool list for the current platform

## What should NOT be added here
- OS install/uninstall logic
- subprocess-based package manager calls
- tool runtime detection logic

Keep this layer pure: "desired state" only.

See `ARCHITECTURE.md`.
