# Agent Instructions (GHDP CLI)

You are working in a repository with an intentionally layered architecture.

## Required reading
- Read `ARCHITECTURE.md` first and follow it strictly.

## Non-negotiable rules
1. Do not reorganize folders/layers unless explicitly asked.
2. Keep CLI commands thin: argument parsing + orchestration only.
3. Put shared cross-cutting concerns in `src/platform_cli/core/`.
4. Manifest schema loading/validation stays in `src/platform_cli/manifests/`.
5. Tool runtime install/detect logic stays in `src/platform_cli/tools/`.
6. All subprocess execution must go through `src/platform_cli/exec/runner.py`.
7. Raise `PlatformError` with stable `code` and `reason` where the meaning is known.
8. Let `PlatformError` bubble to `cli.py::_run()` for formatting.

## If you are unsure where code belongs
Stop and consult `ARCHITECTURE.md` section “Folder-by-folder guide”.

## Release Notes Discipline
9. Manual release notes source is `.github/release-notes/notes.md` (workflow reads this file only).
10. Use `.github/release-notes/template.md` as the structure baseline.
11. For release-candidate work on a feature branch, update `.github/release-notes/notes.md` with latest changes before requesting or triggering a manual release build.
12. Keep `notes.md` markdown clean (headings, bullets, verify commands), and remove unresolved TODO placeholders before release.