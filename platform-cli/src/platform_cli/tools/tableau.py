# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

from datetime import datetime
import importlib.resources as pkg_resources
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

from platform_cli.core.release_content import install_release_content
from platform_cli.core.errors import PlatformError
from platform_cli.state.store import get_tool_state, update_tool_state
from platform_cli.tools.aws_sso import aws_sso_login, ensure_sso_configured, run_aws_cli


def _load_init_config() -> dict[str, Any]:
    try:
        raw = (pkg_resources.files("platform_cli.resources") / "tableau-athena-init.json").read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception as e:
        raise PlatformError(
            f"Unable to load Tableau init configuration: {e}",
            code="E_TABLEAU_INIT_CONFIG_LOAD_FAILED",
            reason="tableau",
        )


def _platform_key() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    return "unsupported"


def _default_drivers_dir(platform_key: str, cfg: dict[str, Any]) -> Path:
    if platform_key == "darwin":
        return Path(cfg["defaults"]["drivers_dir"]["darwin"]).expanduser()
    if platform_key == "linux":
        return Path(cfg["defaults"]["drivers_dir"].get("linux", "/opt/tableau/drivers")).expanduser()
    if platform_key == "windows":
        base = Path(cfg["defaults"]["windows_program_files_default"])
        return base / "Tableau" / "Drivers"
    raise PlatformError(
        f"Tableau init is not supported on platform: {sys.platform}",
        code="E_TABLEAU_PLATFORM_UNSUPPORTED",
        reason="tableau",
    )


def _mac_properties_path(cfg: dict[str, Any]) -> Path:
    return Path(cfg["defaults"]["mac_athena_properties_path"]).expanduser()


def _all_jars_present(directory: Path, jar_names: list[str]) -> bool:
    return all((directory / jar).exists() for jar in jar_names)


def _release_repo(cfg: dict[str, Any]) -> str:
    return str(cfg.get("release_content", {}).get("repo", "")).strip()


def _release_tag(cfg: dict[str, Any]) -> str:
    return str(cfg.get("release_content", {}).get("tag", "")).strip()


def _manifest_asset(cfg: dict[str, Any]) -> str:
    value = str(cfg.get("release_content", {}).get("manifest_asset", "content-manifest.json")).strip()
    return value or "content-manifest.json"


def _resolve_tableau_drivers_root(root_key: str, cfg: dict[str, Any], platform_key: str) -> Path:
    if root_key == "tableau_drivers_root":
        return _default_drivers_dir(platform_key, cfg)
    raise PlatformError(
        f"Unsupported Tableau target root key: {root_key}",
        code="E_TABLEAU_INIT_CONFIG_INVALID",
        reason="tableau",
    )


def _download_release_jars(
    *,
    cfg: dict[str, Any],
    dry_run: bool,
    platform_key: str,
) -> tuple[Path, list[str], dict[str, Any] | None]:
    repo = _release_repo(cfg)
    tag = _release_tag(cfg)
    manifest_asset = _manifest_asset(cfg)
    if not repo or not tag:
        raise PlatformError(
            "Tableau release content repo/tag is not configured.",
            code="E_TABLEAU_RELEASE_URL_NOT_CONFIGURED",
            reason="tableau",
        )

    if dry_run:
        target_root = _default_drivers_dir(platform_key, cfg)
        messages = [
            f"Using release assets from: {repo}@{tag}",
            f"Would resolve release content into: {target_root}",
        ]
        return target_root, messages, None

    result = install_release_content(
        capability="tableau-athena-jars",
        repo=repo,
        tag=tag,
        manifest_asset=manifest_asset,
        resolve_root_key=lambda root_key: _resolve_tableau_drivers_root(root_key, cfg, platform_key),
    )
    messages = [f"Using release assets from: {repo}@{tag}"]
    return Path(str(result["target_path"])), messages, result


def _resolve_source_dir(
    *,
    download_dir: Path | None,
    jar_names: list[str],
    cfg: dict[str, Any],
    dry_run: bool,
    platform_key: str,
) -> tuple[Path, list[str], bool, dict[str, Any] | None]:
    messages: list[str] = []
    if download_dir is not None:
        candidate = download_dir.expanduser()
        if _all_jars_present(candidate, jar_names):
            messages.append(f"Using local jars from: {candidate}")
            return candidate, messages, False, None
        messages.append(
            f"Local download directory does not contain all required jars: {candidate}. Falling back to release assets."
        )

    release_dir, release_messages, release_result = _download_release_jars(
        cfg=cfg,
        dry_run=dry_run,
        platform_key=platform_key,
    )
    messages.extend(release_messages)
    return release_dir, messages, True, release_result


def _copy_jars(
    jar_names: list[str],
    download_dir: Path,
    drivers_dir: Path,
    *,
    dry_run: bool,
    force: bool,
    allow_missing_on_dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    skipped: list[str] = []

    for jar in jar_names:
        src = download_dir / jar
        dst = drivers_dir / jar

        if not src.exists() and not (dry_run and allow_missing_on_dry_run):
            raise PlatformError(
                f"Required jar not found: {src}",
                code="E_TABLEAU_JAR_MISSING",
                reason=jar,
            )

        if dst.exists() and not force:
            skipped.append(str(dst))
            continue

        if dry_run:
            copied.append(f"[dry-run] {src} -> {dst}")
            continue

        try:
            shutil.copy2(src, dst)
        except Exception as e:
            raise PlatformError(
                f"Failed to copy jar '{jar}' to drivers directory: {e}",
                code="E_TABLEAU_JAR_COPY_FAILED",
                reason=jar,
            )
        copied.append(str(dst))

    return copied, skipped


def _drivers_dir_for_check(cfg: dict[str, Any], platform_key: str) -> Path:
    st = get_tool_state("tableau")
    saved = (st.get("drivers_dir") or "").strip() if isinstance(st, dict) else ""
    if saved:
        return Path(saved).expanduser()
    return _default_drivers_dir(platform_key, cfg)


def _is_initialized(cfg: dict[str, Any], platform_key: str) -> bool:
    jar_names = list(cfg.get("required_jars", []))
    if not jar_names:
        return False

    drivers_dir = _drivers_dir_for_check(cfg, platform_key)
    if not _all_jars_present(drivers_dir, jar_names):
        return False

    if platform_key == "darwin":
        props_path = _mac_properties_path(cfg)
        if not props_path.exists():
            return False
        expected_line = cfg["defaults"]["athena_properties_line"]
        try:
            content = props_path.read_text(encoding="utf-8")
        except Exception:
            return False
        if expected_line not in content:
            return False

    return True


def _apply_mac_properties(cfg: dict[str, Any], *, dry_run: bool) -> tuple[bool, str | None, str]:
    path = _mac_properties_path(cfg)
    value = cfg["defaults"]["athena_properties_line"]
    backup: str | None = None

    if dry_run:
        return True, None, str(path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise PlatformError(
            f"Could not create datasource directory for athena.properties: {e}",
            code="E_TABLEAU_PROPS_DIR_CREATE_FAILED",
            reason="tableau",
        )

    if path.exists():
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        backup_path = path.with_name(f"{path.name}.{stamp}.bak")
        try:
            shutil.copy2(path, backup_path)
            backup = str(backup_path)
        except Exception as e:
            raise PlatformError(
                f"Could not back up existing athena.properties: {e}",
                code="E_TABLEAU_PROPS_BACKUP_FAILED",
                reason="tableau",
            )

    try:
        path.write_text(value + "\n", encoding="utf-8")
    except Exception as e:
        raise PlatformError(
            f"Could not write athena.properties: {e}",
            code="E_TABLEAU_PROPS_WRITE_FAILED",
            reason="tableau",
        )
    return True, backup, str(path)


def init(
    *,
    download_dir: Path | None = None,
    drivers_dir: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    cfg = _load_init_config()
    jar_names = list(cfg.get("required_jars", []))
    if not jar_names:
        raise PlatformError(
            "No required jar names configured for Tableau initialization.",
            code="E_TABLEAU_INIT_CONFIG_INVALID",
            reason="tableau",
        )

    platform_key = _platform_key()
    if platform_key == "unsupported":
        raise PlatformError(
            f"Tableau init is not supported on platform: {sys.platform}",
            code="E_TABLEAU_PLATFORM_UNSUPPORTED",
            reason="tableau",
        )

    source_dir, source_messages, source_is_release, release_result = _resolve_source_dir(
        download_dir=download_dir,
        jar_names=jar_names,
        cfg=cfg,
        dry_run=dry_run,
        platform_key=platform_key,
    )
    final_drivers_dir = (drivers_dir.expanduser() if drivers_dir else _default_drivers_dir(platform_key, cfg))
    final_download_dir = source_dir

    if not dry_run:
        try:
            final_drivers_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise PlatformError(
                f"Failed to create Tableau drivers directory '{final_drivers_dir}': {e}",
                code="E_TABLEAU_DRIVERS_DIR_CREATE_FAILED",
                reason="tableau",
            )

    if source_is_release and not dry_run:
        copied = []
        skipped = []
    else:
        copied, skipped = _copy_jars(
            jar_names=jar_names,
            download_dir=final_download_dir,
            drivers_dir=final_drivers_dir,
            dry_run=dry_run,
            force=force,
            allow_missing_on_dry_run=source_is_release,
        )

    props_updated = False
    props_backup: str | None = None
    props_path: str | None = None
    if platform_key == "darwin":
        props_updated, props_backup, props_path = _apply_mac_properties(cfg, dry_run=dry_run)

    messages: list[str] = []
    if dry_run:
        messages.append("DRY RUN: no files were changed.")
    messages.extend(source_messages)
    messages.append(f"Tableau drivers directory: {final_drivers_dir}")
    if source_is_release and not dry_run and release_result is not None:
        messages.append(f"Jar install path: {final_download_dir}")
        messages.append(f"Jar files processed: {int(release_result.get('file_count', 0))}")
        messages.append(f"Jar files installed or updated: {int(release_result.get('updated_count', 0))}")
        messages.append(f"Jar files already present: {int(release_result.get('file_count', 0)) - int(release_result.get('updated_count', 0))}")
    else:
        messages.append(f"Jar sources directory: {final_download_dir}")
        messages.append(f"Jar files processed: {len(copied) + len(skipped)}")
        messages.append(f"Jar files copied: {len(copied)}")
        messages.append(f"Jar files skipped: {len(skipped)}")
        if skipped:
            messages.append("Skipped existing jar files (use --force to overwrite):")
            for item in skipped:
                messages.append(f"  - {item}")

    if platform_key == "darwin":
        messages.append("Mac athena.properties updated.")
        if props_backup:
            messages.append(f"Backup created: {props_backup}")
        if props_path:
            messages.append(f"Properties path: {props_path}")
    else:
        messages.append("Step 2 skipped on Windows (Mac-only athena.properties update).")

    if not dry_run:
        update_tool_state(
            "tableau",
            {
                "initialized": True,
                "initialized_at": int(time.time()),
                "platform": platform_key,
                "drivers_dir": str(final_drivers_dir),
                "properties_path": props_path or "",
                "source_mode": "release" if source_is_release else "local",
            },
        )

    return {
        "success": True,
        "dry_run": dry_run,
        "platform": platform_key,
        "download_dir": str(final_download_dir),
        "drivers_dir": str(final_drivers_dir),
        "jars_copied": copied,
        "jars_skipped": skipped,
        "properties_updated": props_updated,
        "properties_backup": props_backup,
        "properties_path": props_path,
        "messages": messages,
    }


def ensure_initialized_for_login() -> dict[str, Any]:
    cfg = _load_init_config()
    platform_key = _platform_key()
    if platform_key == "unsupported":
        raise PlatformError(
            f"Tableau is not supported on platform: {sys.platform}",
            code="E_TABLEAU_PLATFORM_UNSUPPORTED",
            reason="tableau",
        )

    if _is_initialized(cfg, platform_key):
        update_tool_state(
            "tableau",
            {
                "initialized": True,
                "last_verified_at": int(time.time()),
                "platform": platform_key,
            },
        )
        return {"initialized_now": False, "messages": ["Tableau init already completed. Skipping pre-hook init."]}

    result = init(download_dir=None, drivers_dir=None, dry_run=False, force=False)
    result["initialized_now"] = True
    return result


def refresh_credentials_for_tableau(*, profile: str) -> dict[str, Any]:
    """
    Refresh AWS SSO credentials and sync temporary values into default profile.
    This mirrors the documented tableaulogin behavior used for Tableau Athena auth.
    """
    try:
        ensure_sso_configured(profile=profile)
    except PlatformError as e:
        raise PlatformError(
            f"AWS SSO profile setup failed for profile '{profile}': {e}",
            code="E_TABLEAU_AWS_SSO_CONFIG_FAILED",
            reason="tableau",
        )

    try:
        aws_sso_login(profile=profile)
    except PlatformError as e:
        raise PlatformError(
            f"AWS SSO login failed for profile '{profile}': {e}",
            code="E_TABLEAU_AWS_LOGIN_FAILED",
            reason="tableau",
        )

    try:
        exported = run_aws_cli(
            ["configure", "export-credentials", "--profile", profile, "--output", "json"],
            capture=True,
            check=True,
        )
    except PlatformError as e:
        raise PlatformError(
            f"Could not export credentials for profile '{profile}': {e}",
            code="E_TABLEAU_EXPORT_CREDENTIALS_FAILED",
            reason="tableau",
        )

    try:
        payload = json.loads((exported.stdout or "").strip() or "{}")
        access_key = str(payload.get("AccessKeyId", "")).strip()
        secret_key = str(payload.get("SecretAccessKey", "")).strip()
        session_token = str(payload.get("SessionToken", "")).strip()
        expiration = str(payload.get("Expiration", "")).strip()
    except Exception as e:
        raise PlatformError(
            f"Invalid credentials JSON returned for profile '{profile}': {e}",
            code="E_TABLEAU_CREDENTIALS_JSON_INVALID",
            reason="tableau",
        )

    if not access_key or not secret_key or not session_token:
        raise PlatformError(
            f"Exported credentials for profile '{profile}' are missing required fields.",
            code="E_TABLEAU_CREDENTIALS_JSON_INVALID",
            reason="tableau",
        )

    try:
        run_aws_cli(
            ["configure", "set", "aws_access_key_id", access_key, "--profile", "default"],
            capture=False,
            check=True,
        )
        run_aws_cli(
            ["configure", "set", "aws_secret_access_key", secret_key, "--profile", "default"],
            capture=False,
            check=True,
        )
        run_aws_cli(
            ["configure", "set", "aws_session_token", session_token, "--profile", "default"],
            capture=False,
            check=True,
        )
    except PlatformError as e:
        raise PlatformError(
            f"Failed to sync temporary credentials into AWS default profile: {e}",
            code="E_TABLEAU_DEFAULT_PROFILE_SYNC_FAILED",
            reason="tableau",
        )

    now = int(time.time())
    update_tool_state(
        "tableau",
        {
            "aws_profile_source": profile,
            "default_sync_last_at": now,
            "default_sync_status": "ok",
            "default_sync_session_expiration": expiration,
        },
    )

    messages = [
        f"AWS SSO login succeeded for profile '{profile}'.",
        "Temporary credentials were synced to AWS profile 'default' for Tableau.",
    ]
    if expiration:
        messages.append(f"Credential expiration (UTC): {expiration}")

    return {
        "success": True,
        "profile": profile,
        "target_profile": "default",
        "expiration": expiration,
        "messages": messages,
    }
