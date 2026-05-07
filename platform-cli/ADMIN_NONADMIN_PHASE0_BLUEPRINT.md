# GHDP Admin / Non-Admin Phase 0 Blueprint

## Goal

Implement a phase-0 admin vs non-admin control plane for GHDP CLI that is:

- small enough to ship safely in pre-release,
- visible to users and coding agents,
- aligned with the current layered architecture,
- and limited to phase-0 static personas and static capability enforcement.

## Phase 0 scope

Phase 0 will implement:

1. Actor resolution from the authenticated GitHub user.
2. Static admin vs non-admin personas from bundled resource data plus env inputs.
3. Static capability mapping for a focused set of commands and config writes.
4. Team lock behavior for non-admin users after initial team selection.
5. Temporary elevation through a signed, time-bound admin-issued token.
6. A user-visible read-only command to inspect the active admin/non-admin state.

Phase 0 will not implement:

- remote policy fetch and cache,
- dynamic team policy documents,
- filtered `ghdp commands` visibility by persona,
- config list filtering,
- persistent token activation beyond the current shell environment,
- broad repo-by-repo authorization logic.

## Exact behavior to add

### 1. Actor identity

- Resolve the active actor from GitHub CLI using `gh api user -q .login`.
- Support a test/dev override env var for deterministic tests.
- Treat the actor as admin when their GitHub login is present in a comma-separated `GHDP_ADMIN_USERS` env var.
- Otherwise treat them as non-admin.

Failure behavior:

- If GitHub CLI is missing or unauthenticated, GHDP must degrade to non-admin mode.
- Read-only and non-privileged flows must continue to work.
- Privileged commands must still fail with a clear access or identity message that explains GitHub identity could not be confirmed.
- GHDP must not broadly fail startup just because actor identity could not be resolved.

### 2. Capability model

Use a phase-0 static capability map stored as bundled resource data and loaded through a small core access service.

Architecture note:

- This keeps the phase-0 policy static, but still follows the repo rule to prefer data/resources over pure hardcoded logic when the shape is expressible as data.
- Phase 0 intentionally does not add remote policy fetch or dynamic policy sync yet.

Non-admin baseline capabilities:

- `admin.view`
- `team.initial_select`
- `config.user_safe_write`
- `tools.install`
- `tools.read`
- `repo.read`
- `sync.read`

Admin baseline capabilities:

- all non-admin capabilities
- `team.switch`
- `config.admin_write`
- `tools.uninstall`
- `repo.fix`
- `repo.accept`
- `sync.mutate`
- `publish.execute`
- `admin.token.issue`

Temporary elevation grants may add:

- `team.switch`
- `config.admin_write`
- `tools.uninstall`
- `repo.fix`
- `repo.accept`
- `sync.mutate`
- `publish.execute`

### 3. Temporary elevation token

- Add admin-issued signed tokens using a shared secret from:
  - `GHDP_ADMIN_TOKEN_SECRET`, or
  - `GHDP_ADMIN_TOKEN_SECRET_ID` via the existing secret resolver.
- Token payload includes:
  - actor GitHub login,
  - granted capabilities,
  - optional team restriction,
  - `issued_at`,
  - `expires_at`.
- Phase 0 activation path is shell-scoped via `GHDP_ADMIN_TOKEN`.
- If the token is expired, malformed, signed with the wrong secret, or mismatched to the current actor, it is ignored with a clear denial reason when used.

### 4. Visibility

Add a new read-only command:

- `ghdp admin view`

It will display:

- actor GitHub login,
- persona (`admin` or `non-admin`),
- selected team,
- whether non-admin team switching is locked,
- whether an elevation token is active,
- token expiry if present,
- effective capabilities.

### 5. Enforcement points

#### Team selection

- `ghdp team use`
  - non-admin with no saved team: allowed
  - non-admin with existing saved team: denied unless an elevation token grants `team.switch`
  - admin: always allowed

#### Config governance

Protected config writes must be enforced through a shared core write path, not only in command handlers.

Implementation rule:

- introduce a single core policy-aware config write helper and route governed config mutations through it,
- keep direct policy logic out of individual commands as much as possible,
- keep command modules thin and focused on UX.

- `ghdp config precommit --mode off`
  - admin or elevated `config.admin_write`: allowed
  - non-admin: denied
- `ghdp config precommit --mode warn|enforce`
  - allowed for admin and non-admin
- `ghdp config git-strict-clean --disabled`
  - admin or elevated `config.admin_write`: allowed
  - non-admin: denied
- `ghdp config git-strict-clean --enabled`
  - allowed for admin and non-admin

#### Command capability gates

- `ghdp tools uninstall` -> `tools.uninstall`
- `ghdp repo fix` -> `repo.fix`
- `ghdp repo accept` -> `repo.accept`
- `ghdp sync update` -> `sync.mutate`
- `ghdp sync repair` -> `sync.mutate`
- `ghdp sync run` -> `sync.mutate`
- `ghdp publish` -> `publish.execute`
- `ghdp admin create-token` -> `admin.token.issue`

## Implementation shape

### New core module

Add a new `core` module for access control responsibilities:

- actor resolution,
- persona resolution,
- capability evaluation,
- token minting and verification,
- shared denial messages.

This belongs in `core` because it is a cross-cutting runtime concern shared across commands and selected helpers.

Add a core policy-aware config write helper so governance rules are enforced centrally for protected config keys.

### New decorator

Add a reusable decorator in `core/decorators.py`:

- `@requires_capability("repo.fix")`

This keeps commands thin and aligns with the architecture guidance.

### Command updates

Keep command modules thin:

- declare capabilities,
- call the shared access helpers,
- keep business rules in `core`.

### Resource updates

Add bundled static policy resource data for phase 0, for example:

- persona and capability defaults,
- optionally command-to-capability mappings if that reduces duplicated code.

The admin user list itself remains env-driven in phase 0 so we do not commit personal usernames into the repo.

### Docs updates

Update `README.md` with:

- the phase-0 admin model,
- required env vars,
- example token issuance and use,
- the new `ghdp admin view` command,
- the new `ghdp admin create-token` command,
- and the changed behavior for these existing commands:
  - `ghdp team use`
  - `ghdp config precommit`
  - `ghdp config git-strict-clean`
  - `ghdp tools uninstall`
  - `ghdp repo fix`
  - `ghdp repo accept`
  - `ghdp sync update`
  - `ghdp sync repair`
  - `ghdp sync run`
  - `ghdp publish`

## Files expected to change

- `platform-cli/README.md`
- `platform-cli/src/platform_cli/core/decorators.py`
- `platform-cli/src/platform_cli/core/config.py`
- `platform-cli/src/platform_cli/core/team_context.py`
- `platform-cli/src/platform_cli/commands/team.py`
- `platform-cli/src/platform_cli/commands/config_cli.py`
- `platform-cli/src/platform_cli/commands/tools.py`
- `platform-cli/src/platform_cli/commands/repo.py`
- `platform-cli/src/platform_cli/commands/sync.py`
- `platform-cli/src/platform_cli/commands/publish.py`
- `platform-cli/src/platform_cli/commands/` new admin command module
- `platform-cli/src/platform_cli/core/` new access-control module
- `platform-cli/src/platform_cli/resources/` new phase-0 access policy resource file(s)
- tests for the new access layer and gated commands

## Acceptance criteria

1. A non-admin can select a team once, but cannot switch teams afterward.
2. An admin can switch teams at any time.
3. A non-admin cannot disable `git.strict_clean`.
4. A non-admin cannot set `precommit.mode=off`.
5. A non-admin cannot run `tools uninstall`, `repo fix`, `repo accept`, `sync update`, `sync repair`, `sync run`, or `publish`.
6. An admin can run those commands.
7. An admin can mint a signed token granting a subset of elevated capabilities.
8. A non-admin using `GHDP_ADMIN_TOKEN` with a valid unexpired token gains only the capabilities named in the token.
9. An expired or invalid token does not grant access.
10. `ghdp admin view` shows the active actor, persona, team, token state, and effective capabilities.
11. README guidance matches the shipped behavior.

## Verification plan

- Unit tests for actor resolution, capability resolution, token creation, token verification, and team-lock logic.
- CLI tests for:
  - `ghdp team use`
  - `ghdp config precommit`
  - `ghdp config git-strict-clean`
  - `ghdp admin view`
- Command denial tests for:
  - `tools uninstall`
  - `repo fix`
  - `repo accept`
  - `sync update`
  - `publish`
- Fresh `pipx uninstall ghdp` then `pipx install --force .`
- Manual command verification in a clean GHDP install context with:
  - non-admin flow,
  - admin flow,
  - temporary elevation flow.
