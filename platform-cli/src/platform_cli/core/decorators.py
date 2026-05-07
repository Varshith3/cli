# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/core/decorators.py
from __future__ import annotations

from platform_cli.exec.runner import run_cmd, PlatformError as RunnerPlatformError
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from .errors import PlatformError
from .telemetry import log_usage
from .config import get_bool, get_value
from .context import ctx as cli_ctx

# interactive confirmation
from rich.prompt import Confirm
from rich import print as rprint

CommandFunc = Callable[..., Any]

# Global registry of command metadata, used for:
# - `ghdp commands`
# - per-command config overrides
COMMAND_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _command_meta_payload(fn: CommandFunc) -> Dict[str, Any] | None:
    meta = getattr(fn, "__ghdp_meta__", None)
    if isinstance(meta, dict):
        return meta
    command_name = getattr(fn, "__ghdp_command_name__", None)
    if isinstance(command_name, str) and command_name:
        registry_meta = COMMAND_REGISTRY.get(command_name)
        if isinstance(registry_meta, dict):
            return registry_meta
    return None


def _annotate_required_capability(fn: CommandFunc, capability: str) -> None:
    meta = _command_meta_payload(fn)
    if meta is not None:
        required = meta.setdefault("required_capabilities", [])
        if capability not in required:
            required.append(capability)
        setattr(fn, "__ghdp_meta__", meta)
    existing = tuple(getattr(fn, "__ghdp_required_capabilities__", ()))
    if capability not in existing:
        setattr(fn, "__ghdp_required_capabilities__", existing + (capability,))


def _annotate_release_gate(
    fn: CommandFunc,
    *,
    preview_capability: str,
    allow_admin_bypass: bool,
    allow_ci_bypass: bool,
    command_name: str,
) -> None:
    annotation = {
        "command_name": command_name,
        "preview_capability": preview_capability,
        "allow_admin_bypass": allow_admin_bypass,
        "allow_ci_bypass": allow_ci_bypass,
    }
    meta = _command_meta_payload(fn)
    if meta is not None:
        gates = meta.setdefault("release_gates", [])
        if annotation not in gates:
            gates.append(annotation)
        setattr(fn, "__ghdp_meta__", meta)
    existing = list(getattr(fn, "__ghdp_release_gates__", ()))
    if annotation not in existing:
        existing.append(annotation)
        setattr(fn, "__ghdp_release_gates__", tuple(existing))


def interactive_precondition(
    check_name: str,
    tags: Optional[List[str]] = None,
    check_and_fix: Optional[Callable[..., bool]] = None,
) -> Callable[[CommandFunc], CommandFunc]:
    """
    Decorator for interactive precondition checks.
    - check_name: Name of the check (e.g., "workspace")
    - tags: List of tags describing the requirement
    - check_and_fix: Function that performs the check and fix. Should return True if fixed/ok, False if not.
    """
    tags = tags or []
    def decorator(fn: CommandFunc) -> CommandFunc:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if check_and_fix:
                ok = check_and_fix(*args, **kwargs)
                if not ok:
                    raise PlatformError(
                        f"{check_name} precondition not met and user declined fix.",
                        code=f"E_{check_name.upper()}_PRECONDITION",
                        reason=f"{check_name}_not_met",
                    )
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def command_meta(
    name: str,
    category: str,
    description: str,
    tags: Optional[List[str]] = None,
    aliases: Optional[List[str]] = None,
    config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Callable[[CommandFunc], CommandFunc]:
    """
    Attach metadata to a command and register it centrally.

    - name: CLI command name (e.g. "tf-plan")
    - category: logical group (e.g. "terraform")
    - description: short human readable summary
    - tags: list of keywords
    - config_overrides: per-command config overrides, e.g.:

        config_overrides={
            "precommit.mode": "warn",
            "telemetry.enabled": False,
            "git.strict_clean": False,
        }

      Resolution order at runtime (for a key K):
        1) overrides[K] from this dict
        2) config.json → "K.<command_name>" (e.g. "precommit.mode.tf-plan")
        3) config.json → "K"
        4) hardcoded default
    """
    tags = tags or []
    aliases = [str(alias).strip() for alias in (aliases or []) if str(alias).strip()]
    config_overrides = config_overrides or {}

    def decorator(fn: CommandFunc) -> CommandFunc:
        meta = {
            "name": name,
            "category": category,
            "description": description,
            "tags": tags,
            "aliases": aliases,
            "config_overrides": config_overrides,
        }
        existing_capabilities = [
            str(item).strip()
            for item in getattr(fn, "__ghdp_required_capabilities__", ())
            if str(item).strip()
        ]
        if existing_capabilities:
            meta["required_capabilities"] = existing_capabilities
        existing_release_gates = [dict(item) for item in getattr(fn, "__ghdp_release_gates__", ()) if isinstance(item, dict)]
        if existing_release_gates:
            meta["release_gates"] = existing_release_gates
        COMMAND_REGISTRY[name] = meta

        # Attach metadata to the function as well (in case someone wants it)
        setattr(fn, "__ghdp_command_name__", name)
        setattr(fn, "__ghdp_meta__", meta)

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Nothing special at call time; we mainly use the registry + ctx.
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator

def get_effective_config(
    key: str,
    command_name: Optional[str] = None,
    default: Any = None,
    ) -> Any:
    """
    Compute the 'effective' value of a config key for a given command.

    Resolution order:
      1) decorator-level overrides in COMMAND_REGISTRY[command]["config_overrides"]
      2) config.json key "<key>.<command_name>"  (e.g. "precommit.mode.tf-plan")
      3) config.json key "<key>"
      4) hardcoded default
    """
    # If command_name isn't passed, try to read from CLI context.
    if command_name is None:
        command_name = getattr(cli_ctx, "current_command_name", None)

    # 1) decorator-level overrides
    if command_name:
        meta = COMMAND_REGISTRY.get(command_name) or {}
        overrides = meta.get("config_overrides") or {}
        if key in overrides:
            return overrides[key]

    # 2) per-command override from config.json
    if command_name:
        specific_key = f"{key}.{command_name}"
        val = get_value(specific_key, None)
        if val is not None:
            return val

    # 3) global key in config.json
    val = get_value(key, None)
    if val is not None:
        return val

    # 4) hardcoded default
    return default

def tracked_command(command_name: str) -> Callable[[CommandFunc], CommandFunc]:
    """
    Decorator for top-level commands.

    - Sets cli_ctx.current_command_name for downstream logic.
    - Logs usage for every invocation (success + errors).
    - Understands typical `service` / `env` kwargs for telemetry.
    - Lets PlatformError bubble so cli._run() can render a nice panel.
    """

    def decorator(fn: CommandFunc) -> CommandFunc:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Expose current command to the rest of the system
            cli_ctx.current_command_name = command_name

            service = kwargs.get("service")
            env = kwargs.get("env")

            try:
                result = fn(*args, **kwargs)
            except PlatformError as e:
                # Structured error telemetry
                log_usage(
                    command=command_name,
                    service=service,
                    env=env,
                    status="error",
                    error_code=e.code,
                    reason=e.reason,
                )
                raise
            except Exception as e:
                # Unexpected failure
                log_usage(
                    command=command_name,
                    service=service,
                    env=env,
                    status="error",
                    error_code="E_UNEXPECTED",
                    reason=e.__class__.__name__,
                )
                raise
            else:
                # Success
                log_usage(
                    command=command_name,
                    service=service,
                    env=env,
                    status="ok",
                )
                return result

        return wrapper  # type: ignore[return-value]

    return decorator

def requires_clean_git() -> Callable[[CommandFunc], CommandFunc]:
    """
    Decorator to enforce a clean git working tree before running a command.

    Behaviour is driven by config:
      - global:       git.strict_clean
      - per-command:  git.strict_clean.<command_name>

    Example overrides:

        # In code (per-command)
        @command_meta(..., config_overrides={"git.strict_clean": False})

        # Or in ~/.ghdp/config.json:
        {
          "git.strict_clean": true,
          "git.strict_clean.tf-init": false
        }

    If git is missing or `git status` fails, we *do not* block the command.
    """

    def decorator(fn: CommandFunc) -> CommandFunc:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Check effective strictness for this command
            strict = get_effective_config("git.strict_clean", default=True)
            if not strict:
                return fn(*args, **kwargs)


            try:
                res = run_cmd([
                    "git", "status", "--porcelain"
                ], check=False, capture=True, text=True)
            except RunnerPlatformError as e:
                # Git not installed or command failed – don't block
                return fn(*args, **kwargs)
            except Exception:
                # Any other error – don't block
                return fn(*args, **kwargs)

            if res.returncode != 0:
                # Something odd, but don't block
                return fn(*args, **kwargs)

            if res.stdout.strip():
                # There are uncommitted changes
                raise PlatformError(
                    "Uncommitted changes detected in this repository. "
                    "Please commit or stash before running this command, "
                    "or disable the check via:\n\n"
                    "  ghdp config git-strict-clean --disabled\n\n"
                    "or set a per-command override in ~/.ghdp/config.json.",
                    code="E_GIT_DIRTY",
                    reason="GIT_WORKING_DIR_NOT_CLEAN",
                )

            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator

def dangerous_command(command_name: str | None = None) -> Callable[[CommandFunc], CommandFunc]:
    """
    Extra guard for potentially destructive commands.

    Respects config key `confirm.dangerous` (bool, default True).
    When GHDP_NON_INTERACTIVE=1 and confirm.dangerous=True, we block instead
    of prompting.
    """
    from .context import ctx  # local import to avoid circular deps

    def decorator(fn: CommandFunc) -> CommandFunc:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # allow global opt-out
            if not get_bool("confirm.dangerous", default=True):
                return fn(*args, **kwargs)

            # in CI / non-interactive → block instead of prompting
            if getattr(ctx, "non_interactive", False):
                raise PlatformError(
                    "Dangerous command cannot run in non-interactive mode "
                    "while 'confirm.dangerous' is enabled.",
                    code="E_CONFIRM_REQUIRED",
                    reason="DANGEROUS_COMMAND_NON_INTERACTIVE",
                )

            label = command_name or fn.__name__
            rprint(
                f"[bold yellow]About to run potentially destructive command[/bold yellow]: "
                f"[cyan]{label}[/cyan]"
            )
            ok = Confirm.ask("Proceed?", default=False)
            if not ok:
                raise PlatformError(
                    "Operation cancelled by user.",
                    code="E_CANCELLED",
                    reason="USER_ABORTED",
                )

            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def requires_capability(
    capability: str,
    *,
    team_kwarg: str | None = "team",
    on_denied: Callable[[Any], Any] | None = None,
    interactive: bool = True,
) -> Callable[[CommandFunc], CommandFunc]:
    """
    Enforce an access capability before running a command.

    - capability: shared access capability name
    - team_kwarg: optional kwarg name whose value should be used for team-scoped evaluation
    """

    def decorator(fn: CommandFunc) -> CommandFunc:
        _annotate_required_capability(fn, capability)

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from .access import evaluate_capability_requirement

            team_value = kwargs.get(team_kwarg) if team_kwarg else None
            label = (
                getattr(fn, "__ghdp_command_name__", None)
                or getattr(cli_ctx, "current_command_name", None)
                or getattr(fn, "__name__", capability)
            )
            decision = evaluate_capability_requirement(
                capability,
                team=team_value,
                command_name=label,
                interactive=interactive,
            )
            if decision.status != "allowed":
                if on_denied is not None:
                    return on_denied(decision)
                raise PlatformError(
                    decision.message,
                    code=decision.code,
                    reason=decision.reason,
                )
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def requires_release_gate(
    *,
    preview_capability: str | None = None,
    allow_admin_bypass: bool = True,
    allow_ci_bypass: bool = False,
    team_kwarg: str | None = "team",
    command_name: str | None = None,
    on_denied: Callable[[Any], Any] | None = None,
) -> Callable[[CommandFunc], CommandFunc]:
    """
    Enforce release-channel availability for a command or subcommand.

    Phase 1 keeps this opt-in and decorator-based so command modules stay explicit
    and GHDP does not gain a new global command-dispatch layer.
    """

    def decorator(fn: CommandFunc) -> CommandFunc:
        annotated_command_name = (
            command_name
            or getattr(fn, "__ghdp_command_name__", None)
            or getattr(fn, "__name__", "command")
        )
        _annotate_release_gate(
            fn,
            preview_capability=str(preview_capability or "").strip(),
            allow_admin_bypass=allow_admin_bypass,
            allow_ci_bypass=allow_ci_bypass,
            command_name=str(annotated_command_name),
        )

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from .release_policy import evaluate_release_gate

            team_value = kwargs.get(team_kwarg) if team_kwarg else None
            label = (
                command_name
                or getattr(fn, "__ghdp_command_name__", None)
                or getattr(cli_ctx, "current_command_name", None)
                or getattr(fn, "__name__", "command")
            )
            decision = evaluate_release_gate(
                label,
                preview_capability=preview_capability,
                allow_admin_bypass=allow_admin_bypass,
                allow_ci_bypass=allow_ci_bypass,
                team=team_value,
            )
            if decision.status == "blocked":
                if on_denied is not None:
                    return on_denied(decision)
                raise PlatformError(
                    decision.message,
                    code="E_RELEASE_CHANNEL_BLOCKED",
                    reason=decision.command_name,
                )
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def feature_flag(
    flag_key: str,
    *,
    default: bool = True,
    mode: str = "fail",   # "fail" or "warn"
    ) -> Callable[[CommandFunc], CommandFunc]:
    """
    Gate a command (or behaviour) behind a boolean feature flag in config.

    Example:
        @feature_flag("features.install_tools", default=False, mode="fail")
    """

    def decorator(fn: CommandFunc) -> CommandFunc:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            enabled = get_bool(flag_key, default=default)

            if enabled:
                return fn(*args, **kwargs)

            # Disabled path
            if mode == "warn":
                print(
                    f"[yellow]Feature '{flag_key}' is disabled in config; "
                    f"running command anyway (mode=warn).[/yellow]"
                )
                return fn(*args, **kwargs)

            # Default: hard fail
            raise PlatformError(
                f"Feature '{flag_key}' is currently disabled in GHDP config.",
                code="E_FEATURE_DISABLED",
                reason=flag_key,
            )

        return wrapper  # type: ignore[return-value]

    return decorator
