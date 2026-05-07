# GitHub Copilot Instructions (GHDP CLI)

## Read first
- `ARCHITECTURE.md`

## Architecture rules (must follow)
- `commands/` = Typer wiring + CLI UX (thin)
- `core/` = cross-cutting runtime behavior (context/errors/output/telemetry/update)
- `manifests/` = manifest load + validate + resolve (desired state)
- `tools/` = detect/install/upgrade/uninstall engine (actual state)
- `exec/` = subprocess runner (single source)
- `state/` = persistent GHDP state
- `resources/` = bundled JSON manifests

## Do not do these
- Do not move code across layers as “cleanup”
- Do not introduce a new error class (use `PlatformError`)
- Do not call `subprocess.run` directly (use `run_cmd`)

## Error handling
- Raise `PlatformError(message, code=..., reason=...)` at the layer that knows meaning
- Allow it to bubble to `cli.py::_run()` for consistent printing
