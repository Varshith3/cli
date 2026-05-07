from __future__ import annotations

from datetime import datetime, timezone

import typer

from platform_cli.core.access import (
    classify_token_scope,
    evaluate_token,
    list_token_capability_catalog,
    resolve_access_context,
    resolve_actor,
    setup_local_signer,
    signer_status,
    token_default_ttl_minutes,
)
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import inspect_github_auth
from platform_cli.core.team_context import list_available_teams
from platform_cli.manifests.load import load_manifests
from platform_cli.state.access_session import (
    append_access_event,
    clear_access_session,
    clear_active_token,
    get_access_session,
    get_active_token,
    set_active_token,
)


def fmt_ts(ts: int) -> str:
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_context(ctx, *, include_capabilities: bool = True) -> None:
    gh_auth = inspect_github_auth()
    typer.echo(f"actor: {ctx.actor or '(not resolved)'}")
    typer.echo(f"identity_status: {ctx.identity_status}")
    typer.echo(f"actor_source: {ctx.actor_source}")
    typer.echo(f"base_persona: {ctx.base_persona}")
    typer.echo(f"persona: {ctx.persona}")
    typer.echo(f"active_mode: {ctx.active_mode}")
    typer.echo(f"admin_users_source: {ctx.admin_users_source}")
    typer.echo(f"selected_team: {ctx.selected_team or '(not set)'}")
    typer.echo(f"effective_team: {ctx.effective_team or '(not set)'}")
    if ctx.assumed_team:
        typer.echo(f"assumed_team: {ctx.assumed_team}")
    typer.echo(f"team_locked: {'yes' if ctx.team_locked else 'no'}")
    typer.echo(f"token_status: {ctx.token_status}")
    typer.echo(f"token_source: {ctx.token_source}")
    if ctx.token_scope:
        typer.echo(f"token_scope: {ctx.token_scope}")
    if ctx.token_team:
        typer.echo(f"token_team: {ctx.token_team}")
    if ctx.token_expires_at:
        typer.echo(f"token_expires_at: {fmt_ts(ctx.token_expires_at)}")
    typer.echo(f"release_channel: {ctx.release_channel}")
    typer.echo(f"install_flavor: {gh_auth.install_flavor}")
    typer.echo(f"auth_mode: {gh_auth.auth_mode}")
    typer.echo(f"github_auth_source: {gh_auth.effective_github_auth_source}")
    typer.echo(f"policy_source: {ctx.policy_source}")
    typer.echo(f"release_policy_source: {ctx.release_policy_source}")
    if include_capabilities:
        typer.echo("capabilities:")
        for item in ctx.capabilities:
            typer.echo(f"  - {item}")


def print_access_view() -> None:
    print_context(resolve_access_context())


def validate_team_name(team: str) -> str:
    normalized_team = str(team or "").strip()
    if not normalized_team:
        raise typer.BadParameter("--team is required")

    toolset, _, _ = load_manifests()
    teams = list_available_teams(toolset)
    if normalized_team not in teams:
        raise typer.BadParameter(f"Unknown team '{normalized_team}'. Available teams: {', '.join(teams)}")
    return normalized_team


def _scope_prompt_label(scope: str) -> str:
    mapping = {
        "user": "User only",
        "team": "Team only",
        "user_team": "User + team",
    }
    return mapping.get(scope, scope)


def _interactive_required(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    if bool(getattr(cli_ctx, "non_interactive", False)):
        raise PlatformError(
            f"{field_name} is required in non-interactive mode.",
            code="E_BAD_ARGS",
            reason=field_name,
        )
    return ""


def collect_token_request_inputs(
    *,
    for_user: str,
    capability: list[str],
    ttl_minutes: int,
    team: str,
    interactive_defaults: bool = True,
) -> tuple[str, str, str, list[str], int]:
    normalized_user = str(for_user or "").strip()
    normalized_team = str(team or "").strip()
    requested_capabilities = [str(item).strip() for item in capability if str(item).strip()]
    requested_ttl = int(ttl_minutes or 0)

    if bool(getattr(cli_ctx, "non_interactive", False)) or not interactive_defaults:
        normalized_team = validate_team_name(normalized_team) if normalized_team else ""
        scope = classify_token_scope(normalized_user, normalized_team or None)
        if not scope:
            raise typer.BadParameter("Provide at least one of --for-user or --team")
        if not requested_capabilities:
            raise typer.BadParameter("Provide at least one --capability value")
        effective_ttl = requested_ttl or token_default_ttl_minutes(scope=scope)
        return scope, normalized_user, normalized_team, requested_capabilities, effective_ttl

    if not normalized_user and not normalized_team:
        scope_choice = str(
            typer.prompt(
                "Token scope: 1=user, 2=team, 3=user+team",
                default="1",
                show_default=True,
            )
        ).strip()
        scope_map = {"1": "user", "2": "team", "3": "user_team"}
        scope = scope_map.get(scope_choice, scope_choice.strip().lower())
        if scope not in {"user", "team", "user_team"}:
            raise typer.BadParameter("Choose 1, 2, or 3 for token scope")
        if scope in {"user", "user_team"}:
            normalized_user = str(typer.prompt("GitHub login for token", default="", show_default=False)).strip()
        if scope in {"team", "user_team"}:
            normalized_team = validate_team_name(str(typer.prompt("Team for token")).strip())
    else:
        normalized_team = validate_team_name(normalized_team) if normalized_team else ""
        scope = classify_token_scope(normalized_user, normalized_team or None)
        if not scope:
            raise typer.BadParameter("Provide at least one of --for-user or --team")

    if not requested_capabilities:
        options = list_token_capability_catalog(scope=scope)
        typer.echo("Select capability indexes (comma-separated):")
        for idx, item in enumerate(options, start=1):
            group = item.get("group", "Other")
            label = item.get("label", item["capability"])
            desc = item.get("description", "")
            typer.echo(f"  {idx}. [{group}] {label} :: {desc}")
        selected_raw = str(typer.prompt("Capability indexes")).strip()
        chosen: list[str] = []
        for part in selected_raw.split(","):
            raw = part.strip()
            if not raw:
                continue
            if not raw.isdigit():
                raise typer.BadParameter(f"Invalid capability index '{raw}'")
            index = int(raw)
            if index <= 0 or index > len(options):
                raise typer.BadParameter(f"Capability index '{raw}' is out of range")
            chosen.append(options[index - 1]["capability"])
        requested_capabilities = sorted(set(chosen))
        if not requested_capabilities:
            raise typer.BadParameter("Pick at least one capability")

    effective_ttl = requested_ttl or token_default_ttl_minutes(scope=scope)
    if requested_ttl <= 0 and not bool(getattr(cli_ctx, "non_interactive", False)):
        ttl_prompt = str(
            typer.prompt(
                f"TTL minutes for {_scope_prompt_label(scope)} token",
                default=str(effective_ttl),
                show_default=True,
            )
        ).strip()
        effective_ttl = int(ttl_prompt or effective_ttl)

    return scope, normalized_user, normalized_team, requested_capabilities, effective_ttl


def print_signer_status() -> None:
    status = signer_status()
    typer.echo(f"present: {status.get('present', 'no')}")
    typer.echo(f"key_id: {status.get('key_id') or '(not set)'}")
    typer.echo(f"algorithm: {status.get('algorithm') or '(not set)'}")
    typer.echo(f"private_key_path: {status.get('private_key_path') or '(not set)'}")
    typer.echo(f"metadata_path: {status.get('metadata_path') or '(not set)'}")
    typer.echo(f"policy_active_key_id: {status.get('policy_active_key_id') or '(not set)'}")
    typer.echo(f"policy_has_local_key: {status.get('policy_has_local_key') or 'no'}")


def setup_signer_flow(*, key_id: str, overwrite: bool, update_local_policy: bool) -> None:
    normalized_key_id = str(key_id or "").strip()
    if not normalized_key_id and not bool(getattr(cli_ctx, "non_interactive", False)):
        normalized_key_id = str(
            typer.prompt(
                "Signer key id",
                default=f"local-admin-{int(datetime.now(tz=timezone.utc).timestamp())}",
                show_default=True,
            )
        ).strip()
    normalized_key_id = _interactive_required(normalized_key_id, field_name="--key-id")
    created = setup_local_signer(
        key_id=normalized_key_id,
        overwrite=overwrite,
        update_local_policy=update_local_policy,
    )
    typer.echo("Admin signer setup complete.")
    typer.echo(f"key_id: {created['key_id']}")
    typer.echo(f"algorithm: {created['algorithm']}")
    typer.echo(f"private_key_path: {created['private_key_path']}")
    typer.echo(f"metadata_path: {created['metadata_path']}")
    if created.get("policy_path"):
        typer.echo(f"policy_path: {created['policy_path']}")
    typer.echo("Public verification key:")
    typer.echo(created["public_key_pem"].rstrip())


def activate_token_flow(token: str) -> None:
    actor = resolve_actor(interactive=True)
    if not actor.login:
        raise PlatformError(
            "GitHub identity could not be confirmed. Run 'gh auth login' or provide your GitHub login when prompted.",
            code="E_ACTOR_IDENTITY_REQUIRED",
            reason=actor.status,
        )

    raw_token = str(token or "").strip()
    prompted_for_token = False
    if not raw_token:
        if bool(getattr(cli_ctx, "non_interactive", False)):
            raise PlatformError(
                "Access token is required in non-interactive mode.",
                code="E_ACCESS_TOKEN_REQUIRED",
                reason="access_token",
            )
        prompted_for_token = True
        raw_token = str(typer.prompt("Access token", hide_input=False)).strip()
    if not raw_token:
        raise PlatformError(
            "Access token is required.",
            code="E_ACCESS_TOKEN_REQUIRED",
            reason="access_token",
        )

    evaluation = evaluate_token(raw_token, actor=actor.login, team=None, enforce_team_scope=False)
    if evaluation.status != "active" or not evaluation.claims:
        raise PlatformError(
            evaluation.message or "Access token is invalid.",
            code="E_ACCESS_TOKEN_INVALID",
            reason=evaluation.status or "access_token",
        )

    set_active_token(raw_token)
    append_access_event(
        "access.token_activated",
        {
            "actor": actor.login,
            "scope": evaluation.claims.scope,
            "team": evaluation.claims.team,
            "expires_at": evaluation.claims.expires_at,
            "capabilities": list(evaluation.claims.capabilities),
        },
    )

    typer.echo("Access token activated.")
    if prompted_for_token:
        typer.echo(f"entered_token: {raw_token}")
    typer.echo(f"scope: {evaluation.claims.scope}")
    typer.echo(f"actor: {evaluation.claims.actor or '(not restricted)'}")
    typer.echo(f"expires_at: {fmt_ts(evaluation.claims.expires_at)}")
    typer.echo(f"team: {evaluation.claims.team or '(not restricted)'}")
    typer.echo("granted_capabilities:")
    for item in evaluation.claims.capabilities:
        typer.echo(f"  - {item}")


def clear_active_token_flow() -> None:
    had_state_token = bool(get_active_token().strip())
    clear_active_token()
    append_access_event("access.token_cleared", {"local_token_present": had_state_token})
    typer.echo("Cleared locally stored access token.")


def print_token_status() -> None:
    ctx = resolve_access_context()
    typer.echo(f"token_status: {ctx.token_status}")
    typer.echo(f"token_source: {ctx.token_source}")
    if ctx.token_scope:
        typer.echo(f"token_scope: {ctx.token_scope}")
    if ctx.token_team:
        typer.echo(f"token_team: {ctx.token_team}")
    if ctx.token_expires_at:
        typer.echo(f"token_expires_at: {fmt_ts(ctx.token_expires_at)}")
    raw_token = get_active_token().strip()
    evaluation = evaluate_token(raw_token, actor=ctx.actor, team=None, enforce_team_scope=False)
    if evaluation.claims:
        typer.echo(f"scope: {evaluation.claims.scope}")
        typer.echo(f"actor: {evaluation.claims.actor or '(not restricted)'}")
        typer.echo("granted_capabilities:")
        for item in evaluation.claims.capabilities:
            typer.echo(f"  - {item}")


def print_session_view() -> None:
    session = get_access_session()
    ctx = resolve_access_context(interactive=False, persist_remembered=False)
    typer.echo(f"remembered_actor: {session.get('remembered_actor') or '(not set)'}")
    typer.echo(f"active_token_present: {'yes' if session.get('active_token') else 'no'}")
    typer.echo(f"assumed_team: {session.get('assumed_team') or '(not set)'}")
    print_context(ctx, include_capabilities=False)


def clear_session_flow() -> None:
    session = get_access_session()
    had_any = bool(session.get("remembered_actor") or session.get("active_token") or session.get("assumed_team"))
    clear_access_session()
    append_access_event(
        "access.session_cleared",
        {
            "remembered_actor_present": bool(session.get("remembered_actor")),
            "active_token_present": bool(session.get("active_token")),
            "assumed_team_present": bool(session.get("assumed_team")),
        },
    )
    if had_any:
        typer.echo("Cleared local access-session state.")
    else:
        typer.echo("Access session state was already empty.")
