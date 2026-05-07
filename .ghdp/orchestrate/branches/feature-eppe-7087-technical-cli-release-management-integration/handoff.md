# Handoff

- Ticket: `EPPE-7087`
- Branch: `feature/EPPE-7087-TECHNICAL-cli-release-management-integration`
- Goal: move manual binary-release logic from GitHub Actions into the GHDP CLI so the same capability can run from laptop, GitHub Actions, Jenkins, or another executor.

## What Is In Place

- Repo intent captured in `.ghdp/frbr/intent.json`
- POA captured in `poa.md`
- New `ghdp release` command family added for planning, release preparation, and build/upload
- Release policy moved into bundled CLI resources
- Manual build workflow rewritten to invoke GHDP instead of owning the release logic directly
- Focused release tests added and currently passing

## Latest Validation

- `pytest platform-cli/tests/test_release_binaries.py platform-cli/tests/test_repo_ready.py -q`
- `python -m platform_cli.cli release --help` with `PYTHONPATH=platform-cli/src`
- Workflow YAML parses successfully

## Next Steps

1. Final review for architecture and behavior drift
2. Commit and push the branch changes
3. Guard `pipx` usage and run install-based testing
4. Execute prerelease flow and add Jira / PR comments
