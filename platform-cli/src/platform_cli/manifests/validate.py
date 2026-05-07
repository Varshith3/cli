# NOTE: Architectural rules in ARCHITECTURE.md â€” do not refactor cross-layer.
from __future__ import annotations

from typing import Any, Dict, List, Tuple
import json
from pathlib import Path

# TODO: adjust import to your real PlatformError location
try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover
    class PlatformError(RuntimeError):
        def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert

from platform_cli.manifests.load import current_platform_key, toolset_source_kind


def _require(obj: Dict[str, Any], key: str, *, ctx: str) -> Any:
    if key not in obj:
        raise PlatformError(f"Missing key '{key}' in {ctx}", code="E_MANIFEST_INVALID", reason=f"{ctx}:{key}")
    return obj[key]


def validate_toolset_policy_source(source: str, *, allow_packaged_fallback: bool = True) -> None:
    """
    Validate that a toolset source is trusted for ownership policy decisions.

    The synced managed team-toolset capability is the authoritative source of truth.
    The packaged toolset is allowed only as a bootstrap/fallback copy.
    """
    kind = toolset_source_kind(source)
    trusted_kinds = {"managed"}
    if allow_packaged_fallback:
        trusted_kinds.add("packaged")

    if kind not in trusted_kinds:
        allowed = "managed synced capability" if not allow_packaged_fallback else "managed synced capability or packaged bootstrap fallback"
        raise PlatformError(
            f"Ownership policy must come from the {allowed}; source '{source}' is not trusted for ownership policy.",
            code="E_MANIFEST_INVALID",
            reason=f"toolset_source:{kind}",
        )


def _ownership_signature(toolset: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    signature: Dict[str, Dict[str, Dict[str, Any]]] = {}
    teams = toolset.get("teams", {})
    if not isinstance(teams, dict):
        return signature

    for team, team_obj in teams.items():
        team_signature: Dict[str, Dict[str, Any]] = {}
        if not isinstance(team_obj, dict):
            signature[team] = team_signature
            continue

        tools = team_obj.get("tools", {})
        if not isinstance(tools, dict):
            signature[team] = team_signature
            continue

        for tool_name, req in tools.items():
            ownership = req.get("ownership", {}) if isinstance(req, dict) else {}
            if not isinstance(ownership, dict):
                ownership = {}
            team_signature[tool_name] = {
                "default_owner": ownership.get("default_owner", "ghdp"),
                "allow_user_override": bool(ownership.get("allow_user_override", False)),
            }

        signature[team] = team_signature

    return signature


def validate_toolset_ownership_alignment(packaged_toolset: Dict[str, Any], managed_toolset: Dict[str, Any]) -> None:
    """
    Validate that the packaged bootstrap manifest stays aligned with the managed
    synced team-toolset on ownership policy fields.
    """
    validate_toolset(packaged_toolset)
    validate_toolset(managed_toolset)

    packaged_signature = _ownership_signature(packaged_toolset)
    managed_signature = _ownership_signature(managed_toolset)

    if packaged_signature != managed_signature:
        raise PlatformError(
            "Packaged toolset fallback ownership policy must match the synced managed team-toolset.",
            code="E_MANIFEST_INVALID",
            reason="toolset:ownership_alignment",
        )


def validate_toolset(toolset: Dict[str, Any]) -> None:
    _require(toolset, "schema_version", ctx="toolset")
    teams = _require(toolset, "teams", ctx="toolset")
    if not isinstance(teams, dict) or not teams:
        raise PlatformError("toolset.teams must be a non-empty object", code="E_MANIFEST_INVALID", reason="toolset.teams")

    for team, team_obj in teams.items():
        if not isinstance(team_obj, dict):
            raise PlatformError(f"toolset.teams.{team} must be an object", code="E_MANIFEST_INVALID", reason=f"team:{team}")

        tools = _require(team_obj, "tools", ctx=f"toolset.teams.{team}")
        if not isinstance(tools, dict) or not tools:
            raise PlatformError(
                f"toolset.teams.{team}.tools must be a non-empty object",
                code="E_MANIFEST_INVALID",
                reason=f"team:{team}:tools",
            )
        for tool_name, req in tools.items():
            if not isinstance(req, dict):
                raise PlatformError(
                    f"tool requirement for '{tool_name}' must be an object",
                    code="E_MANIFEST_INVALID",
                    reason=f"{team}:{tool_name}",
                )
            # version requirement optional, but if present validate shape
            if "op" in req or "version" in req:
                op = req.get("op")
                ver = req.get("version")
                if op not in ("==", ">="):
                    raise PlatformError(
                        f"Invalid op for '{tool_name}': {op} (allowed: ==, >=)",
                        code="E_MANIFEST_INVALID",
                        reason=f"{team}:{tool_name}:op",
                    )
                if not isinstance(ver, str) or not ver.strip():
                    raise PlatformError(
                        f"Invalid version for '{tool_name}'",
                        code="E_MANIFEST_INVALID",
                        reason=f"{team}:{tool_name}:version",
                    )
            ownership = req.get("ownership")
            if ownership is not None:
                if not isinstance(ownership, dict):
                    raise PlatformError(
                        f"tool ownership for '{tool_name}' must be an object",
                        code="E_MANIFEST_INVALID",
                        reason=f"{team}:{tool_name}:ownership",
                    )
                default_owner = ownership.get("default_owner", "ghdp")
                if default_owner not in ("ghdp", "user"):
                    raise PlatformError(
                        f"Invalid ownership.default_owner for '{tool_name}': {default_owner}",
                        code="E_MANIFEST_INVALID",
                        reason=f"{team}:{tool_name}:ownership:default_owner",
                    )
                allow_user_override = ownership.get("allow_user_override", False)
                if not isinstance(allow_user_override, bool):
                    raise PlatformError(
                        f"tool ownership.allow_user_override for '{tool_name}' must be a boolean",
                        code="E_MANIFEST_INVALID",
                        reason=f"{team}:{tool_name}:ownership:allow_user_override",
                    )


def validate_registry(registry: Dict[str, Any]) -> None:
    _require(registry, "schema_version", ctx="registry")
    tools = _require(registry, "tools", ctx="registry")
    if not isinstance(tools, dict) or not tools:
        raise PlatformError("registry.tools must be a non-empty object", code="E_MANIFEST_INVALID", reason="registry.tools")

    for name, obj in tools.items():
        if not isinstance(obj, dict):
            raise PlatformError(f"registry.tools.{name} must be an object", code="E_MANIFEST_INVALID", reason=f"tool:{name}")
        _require(obj, "display_name", ctx=f"registry.tools.{name}")
        detect_cmd = _require(obj, "detect_cmd", ctx=f"registry.tools.{name}")
        if not isinstance(detect_cmd, list) or not detect_cmd:
            raise PlatformError(
                f"registry.tools.{name}.detect_cmd must be a non-empty array",
                code="E_MANIFEST_INVALID",
                reason=f"tool:{name}:detect_cmd",
            )
        platforms = _require(obj, "platforms", ctx=f"registry.tools.{name}")
        if not isinstance(platforms, dict) or not platforms:
            raise PlatformError(
                f"registry.tools.{name}.platforms must be a non-empty object",
                code="E_MANIFEST_INVALID",
                reason=f"tool:{name}:platforms",
            )


def validate_team_resolves(team: str, toolset: Dict[str, Any], registry: Dict[str, Any]) -> List[str]:
    """
    Validates:
      - team exists in toolset
      - every tool listed for the team exists in registry
      - tool has commands for current OS and has at least install/detect
    Returns the list of tool names for the team (ordered as in JSON iteration).
    """
    validate_toolset(toolset)
    validate_registry(registry)

    teams = toolset["teams"]
    if team not in teams:
        raise PlatformError(f"Unknown team '{team}'", code="E_TEAM_UNKNOWN", reason=team)

    team_tools = teams[team]["tools"]
    registry_tools = registry["tools"]
    os_key = current_platform_key()

    resolved: List[str] = []
    for tool_name in team_tools.keys():
        if tool_name not in registry_tools:
            raise PlatformError(
                f"Tool '{tool_name}' is in toolset but missing from registry",
                code="E_TOOL_NOT_DEFINED",
                reason=tool_name,
            )

        tool_obj = registry_tools[tool_name]
        platforms = tool_obj.get("platforms", {})
        if os_key not in platforms:
            raise PlatformError(
                f"Tool '{tool_name}' does not support platform '{os_key}'",
                code="E_PLATFORM_UNSUPPORTED",
                reason=f"{tool_name}:{os_key}",
            )

        # Require install/upgrade/uninstall shape later; for now enforce install exists for v0.0.1
        plat = platforms[os_key]
        if "install" not in plat or not isinstance(plat["install"], list) or not plat["install"]:
            raise PlatformError(
                f"Tool '{tool_name}' missing install command for '{os_key}'",
                code="E_MANIFEST_INVALID",
                reason=f"{tool_name}:{os_key}:install",
            )

        resolved.append(tool_name)

    return resolved


def validate_config_defaults(config_defaults: Dict[str, Any]) -> None:
    """
    Validate config defaults manifest schema.

    Expected shape:
      {
        "schema_version": "1.0",
        "defaults": {
          "some.key": <json-serializable value>
        }
      }
    """
    _require(config_defaults, "schema_version", ctx="config_defaults")
    defaults = _require(config_defaults, "defaults", ctx="config_defaults")

    if not isinstance(defaults, dict):
        raise PlatformError(
            "config_defaults.defaults must be an object",
            code="E_MANIFEST_INVALID",
            reason="config_defaults.defaults",
        )

    for key, value in defaults.items():
        if not isinstance(key, str) or not key.strip():
            raise PlatformError(
                "config_defaults.defaults keys must be non-empty strings",
                code="E_MANIFEST_INVALID",
                reason="config_defaults.defaults:key",
            )

        if not isinstance(value, (str, int, float, bool, list, dict)) and value is not None:
            raise PlatformError(
                f"config default '{key}' has unsupported value type",
                code="E_MANIFEST_INVALID",
                reason=f"config_defaults.defaults:{key}",
            )


def validate_claude_athena_workgroup_map(mapping_payload: Dict[str, Any]) -> None:
    version = _require(mapping_payload, "version", ctx="claude_athena_workgroup_map")
    if version != 1:
        raise PlatformError(
            "claude_athena_workgroup_map.version must equal 1",
            code="E_MANIFEST_INVALID",
            reason="claude_athena_workgroup_map.version",
        )

    mappings = _require(mapping_payload, "mappings", ctx="claude_athena_workgroup_map")
    if not isinstance(mappings, list) or not mappings:
        raise PlatformError(
            "claude_athena_workgroup_map.mappings must be a non-empty array",
            code="E_MANIFEST_INVALID",
            reason="claude_athena_workgroup_map.mappings",
        )

    seen: set[tuple[str, str]] = set()
    for idx, entry in enumerate(mappings):
        ctx = f"claude_athena_workgroup_map.mappings[{idx}]"
        if not isinstance(entry, dict):
            raise PlatformError(
                f"{ctx} must be an object",
                code="E_MANIFEST_INVALID",
                reason=ctx,
            )

        account_id = _require(entry, "account_id", ctx=ctx)
        role_name = _require(entry, "role_name", ctx=ctx)
        workgroup = _require(entry, "athena_workgroup", ctx=ctx)

        if not isinstance(account_id, str) or not account_id.isdigit() or len(account_id) != 12:
            raise PlatformError(
                f"{ctx}.account_id must be a 12-digit AWS account id",
                code="E_MANIFEST_INVALID",
                reason=f"{ctx}.account_id",
            )
        if not isinstance(role_name, str) or not role_name.strip():
            raise PlatformError(
                f"{ctx}.role_name must be a non-empty string",
                code="E_MANIFEST_INVALID",
                reason=f"{ctx}.role_name",
            )
        if not isinstance(workgroup, str) or not workgroup.strip():
            raise PlatformError(
                f"{ctx}.athena_workgroup must be a non-empty string",
                code="E_MANIFEST_INVALID",
                reason=f"{ctx}.athena_workgroup",
            )

        signature = (account_id.strip(), role_name.strip())
        if signature in seen:
            raise PlatformError(
                f"Duplicate Claude Athena workgroup mapping for account_id={signature[0]} role_name={signature[1]}",
                code="E_MANIFEST_INVALID",
                reason="claude_athena_workgroup_map.duplicate",
            )
        seen.add(signature)


def validate_apps_config(apps_json_path: Path) -> Dict[str, Any]:
    """
    Validate apps.json against schema.
    
    Returns dict with {"valid": bool, "errors": List[str]}
    """
    try:
        import jsonschema
    except ImportError:
        raise PlatformError(
            "jsonschema package required for data-product manifest validation",
            code="E_DEPENDENCY_MISSING",
            reason="jsonschema",
        )
    
    schema_path = Path(__file__).parent.parent / "resources" / "manifests" / "apps-schema.json"
    
    try:
        with open(schema_path) as f:
            schema = json.load(f)
    except Exception as e:
        raise PlatformError(
            f"Failed to load apps-schema.json: {e}",
            code="E_MANIFEST_INVALID",
            reason="apps-schema",
        )
    
    try:
        with open(apps_json_path) as f:
            data = json.load(f)
    except Exception as e:
        return {"valid": False, "errors": [f"Failed to parse apps.json: {e}"]}
    
    try:
        jsonschema.validate(data, schema)
        return {"valid": True, "errors": []}
    except jsonschema.ValidationError as e:
        return {"valid": False, "errors": [str(e)]}


def validate_infra_config(infra_json_path: Path) -> Dict[str, Any]:
    """
    Validate infra.json against schema.
    
    Returns dict with {"valid": bool, "errors": List[str]}
    """
    try:
        import jsonschema
    except ImportError:
        raise PlatformError(
            "jsonschema package required for data-product manifest validation",
            code="E_DEPENDENCY_MISSING",
            reason="jsonschema",
        )
    
    schema_path = Path(__file__).parent.parent / "resources" / "manifests" / "infra-schema.json"
    
    try:
        with open(schema_path) as f:
            schema = json.load(f)
    except Exception as e:
        raise PlatformError(
            f"Failed to load infra-schema.json: {e}",
            code="E_MANIFEST_INVALID",
            reason="infra-schema",
        )
    
    try:
        with open(infra_json_path) as f:
            data = json.load(f)
    except Exception as e:
        return {"valid": False, "errors": [f"Failed to parse infra.json: {e}"]}
    
    try:
        jsonschema.validate(data, schema)
        return {"valid": True, "errors": []}
    except jsonschema.ValidationError as e:
        return {"valid": False, "errors": [str(e)]}
