# Agent Notes (commands)

This folder defines the CLI surface area (Typer commands).

## What belongs here
- Typer command definitions
- argument parsing
- help text / UX messaging
- calling into services (`tools/`, `manifests/`, `core/`)

## What does NOT belong here
- OS-specific install logic
- direct subprocess calls
- persistence/state writes (except via services)
- manifest schema validation logic

## Rule
Commands should be thin controllers. Put real logic in `tools/` or `core/`.

Read `ARCHITECTURE.md` before changing structure.
