# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from platform_cli.core.errors import PlatformError


def _require(obj: Dict[str, Any], key: str, *, ctx: str) -> Any:
    if key not in obj:
        raise PlatformError(
            f"Missing key '{key}' in {ctx}",
            code="E_REPO_READY_INVALID_SCHEMA",
            reason=f"{ctx}:{key}",
        )
    return obj[key]


def _require_mapping(obj: Dict[str, Any], key: str, *, ctx: str) -> Dict[str, Any]:
    value = _require(obj, key, ctx=ctx)
    if not isinstance(value, dict):
        raise PlatformError(
            f"{ctx}.{key} must be an object",
            code="E_REPO_READY_INVALID_SCHEMA",
            reason=f"{ctx}:{key}",
        )
    return value


def _require_str(obj: Dict[str, Any], key: str, *, ctx: str) -> str:
    value = _require(obj, key, ctx=ctx)
    if not isinstance(value, str) or not value.strip():
        raise PlatformError(
            f"{ctx}.{key} must be a non-empty string",
            code="E_REPO_READY_INVALID_SCHEMA",
            reason=f"{ctx}:{key}",
        )
    return value.strip()


def _require_bool(obj: Dict[str, Any], key: str, *, ctx: str) -> bool:
    value = _require(obj, key, ctx=ctx)
    if not isinstance(value, bool):
        raise PlatformError(
            f"{ctx}.{key} must be a boolean",
            code="E_REPO_READY_INVALID_SCHEMA",
            reason=f"{ctx}:{key}",
        )
    return value


def _require_list_of_str(obj: Dict[str, Any], key: str, *, ctx: str) -> List[str]:
    value = _require(obj, key, ctx=ctx)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PlatformError(
            f"{ctx}.{key} must be a list of strings",
            code="E_REPO_READY_INVALID_SCHEMA",
            reason=f"{ctx}:{key}",
        )
    return list(value)


def _require_list(obj: Dict[str, Any], key: str, *, ctx: str) -> List[Any]:
    value = _require(obj, key, ctx=ctx)
    if not isinstance(value, list):
        raise PlatformError(
            f"{ctx}.{key} must be a list",
            code="E_REPO_READY_INVALID_SCHEMA",
            reason=f"{ctx}:{key}",
        )
    return list(value)


def _validate_command_entries(obj: Dict[str, Any], key: str, *, ctx: str) -> None:
    entries = _require_list(obj, key, ctx=ctx)
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PlatformError(
                f"{ctx}.{key}[{idx}] must be an object",
                code="E_REPO_READY_INVALID_SCHEMA",
                reason=f"{ctx}:{key}:{idx}",
            )
        _require_str(entry, "cmd", ctx=f"{ctx}.{key}[{idx}]")
        if "cwd" in entry and (not isinstance(entry["cwd"], str) or not entry["cwd"].strip()):
            raise PlatformError(
                f"{ctx}.{key}[{idx}].cwd must be a non-empty string when provided",
                code="E_REPO_READY_INVALID_SCHEMA",
                reason=f"{ctx}:{key}:{idx}:cwd",
            )
        if "notes" in entry and (not isinstance(entry["notes"], str) or not entry["notes"].strip()):
            raise PlatformError(
                f"{ctx}.{key}[{idx}].notes must be a non-empty string when provided",
                code="E_REPO_READY_INVALID_SCHEMA",
                reason=f"{ctx}:{key}:{idx}:notes",
            )


def _validate_services(runbook: Dict[str, Any]) -> None:
    if "services" in runbook:
        services = _require_list(runbook, "services", ctx="runbook")
        for idx, service in enumerate(services):
            if not isinstance(service, dict):
                raise PlatformError(
                    f"runbook.services[{idx}] must be an object",
                    code="E_REPO_READY_INVALID_SCHEMA",
                    reason=f"runbook:services:{idx}",
                )
            _require_str(service, "name", ctx=f"runbook.services[{idx}]")
            if "start" in service and (not isinstance(service["start"], str) or not service["start"].strip()):
                raise PlatformError(
                    f"runbook.services[{idx}].start must be a non-empty string when provided",
                    code="E_REPO_READY_INVALID_SCHEMA",
                    reason=f"runbook:services:{idx}:start",
                )
            if "notes" in service and (not isinstance(service["notes"], str) or not service["notes"].strip()):
                raise PlatformError(
                    f"runbook.services[{idx}].notes must be a non-empty string when provided",
                    code="E_REPO_READY_INVALID_SCHEMA",
                    reason=f"runbook:services:{idx}:notes",
                )
        return

    _require_list_of_str(runbook, "required_services", ctx="runbook")


def _validate_allowed(value: str, *, allowed: Iterable[str], ctx: str) -> None:
    allowed_set = set(allowed)
    if value not in allowed_set:
        raise PlatformError(
            f"Invalid value '{value}' for {ctx}; allowed values: {sorted(allowed_set)}",
            code="E_REPO_READY_INVALID_VALUE",
            reason=ctx,
        )


def validate_repo_ready_vocab(vocab: Dict[str, Any]) -> None:
    _require_str(vocab, "schema_version", ctx="repo_ready_vocab")
    for key in ("repo_types", "traits", "risk_tiers", "execution_modes", "tools"):
        values = _require_list_of_str(vocab, key, ctx="repo_ready_vocab")
        if not values:
            raise PlatformError(
                f"repo_ready_vocab.{key} must not be empty",
                code="E_REPO_READY_INVALID_SCHEMA",
                reason=f"repo_ready_vocab:{key}",
            )


def validate_repo_config(config: Dict[str, Any], vocab: Dict[str, Any]) -> None:
    validate_repo_ready_vocab(vocab)
    _require_str(config, "schema_version", ctx="config")

    repo = _require_mapping(config, "repo", ctx="config")
    _require_str(repo, "name", ctx="config.repo")
    repo_type = _require_str(repo, "type", ctx="config.repo")
    _validate_allowed(repo_type, allowed=vocab["repo_types"], ctx="config.repo.type")
    traits = _require_list_of_str(repo, "traits", ctx="config.repo")
    if len(traits) > 4:
        raise PlatformError(
            "config.repo.traits must contain at most 4 values",
            code="E_REPO_READY_INVALID_VALUE",
            reason="config.repo.traits",
        )
    for trait in traits:
        _validate_allowed(trait, allowed=vocab["traits"], ctx="config.repo.traits")

    classification = _require_mapping(config, "classification", ctx="config")
    source = _require_str(classification, "source", ctx="config.classification")
    _validate_allowed(source, allowed=("explicit", "inferred"), ctx="config.classification.source")
    _require_list_of_str(classification, "evidence", ctx="config.classification")

    risk = _require_mapping(config, "risk", ctx="config")
    risk_tier = _require_str(risk, "tier", ctx="config.risk")
    _validate_allowed(risk_tier, allowed=vocab["risk_tiers"], ctx="config.risk.tier")

    execution = _require_mapping(config, "execution", ctx="config")
    execution_mode = _require_str(execution, "mode", ctx="config.execution")
    _validate_allowed(execution_mode, allowed=vocab["execution_modes"], ctx="config.execution.mode")

    enabled = _require_mapping(config, "enabled", ctx="config")
    tools = _require_list_of_str(enabled, "tools", ctx="config.enabled")
    for tool in tools:
        _validate_allowed(tool, allowed=vocab["tools"], ctx="config.enabled.tools")
    _require_list_of_str(enabled, "teams", ctx="config.enabled")
    _require_bool(enabled, "subagents", ctx="config.enabled")

    metadata = _require_mapping(config, "metadata", ctx="config")
    _require_str(metadata, "managed_by", ctx="config.metadata")
    _require_str(metadata, "template_version", ctx="config.metadata")


def validate_guardrails_config(guardrails: Dict[str, Any]) -> None:
    _require_str(guardrails, "schema_version", ctx="guardrails")

    baseline = _require_mapping(guardrails, "baseline", ctx="guardrails")
    enforced = _require_bool(baseline, "enforced", ctx="guardrails.baseline")
    if not enforced:
        raise PlatformError(
            "guardrails.baseline.enforced must be true",
            code="E_REPO_READY_INVALID_VALUE",
            reason="guardrails.baseline.enforced",
        )

    deny = _require_mapping(guardrails, "deny", ctx="guardrails")
    for key in ("folders", "patterns", "commands"):
        _require_list_of_str(deny, key, ctx="guardrails.deny")

    skills = _require_mapping(guardrails, "skills", ctx="guardrails")
    _require_list_of_str(skills, "allow", ctx="guardrails.skills")
    _require_list_of_str(skills, "deny", ctx="guardrails.skills")

    mcp = _require_mapping(guardrails, "mcp", ctx="guardrails")
    _require_list_of_str(mcp, "allow", ctx="guardrails.mcp")
    _require_list_of_str(mcp, "deny", ctx="guardrails.mcp")

    approvals = _require_mapping(guardrails, "approvals", ctx="guardrails")
    confirm_before = _require_list_of_str(approvals, "confirm_before", ctx="guardrails.approvals")
    if "big_change" not in confirm_before:
        raise PlatformError(
            "guardrails.approvals.confirm_before must include 'big_change'",
            code="E_REPO_READY_INVALID_VALUE",
            reason="guardrails.approvals.confirm_before",
        )

    data_safety = _require_mapping(guardrails, "data_safety", ctx="guardrails")
    _require_str(data_safety, "mode", ctx="guardrails.data_safety")
    _require_bool(data_safety, "pii", ctx="guardrails.data_safety")
    _require_bool(data_safety, "phi", ctx="guardrails.data_safety")

    metadata = _require_mapping(guardrails, "metadata", ctx="guardrails")
    _require_str(metadata, "managed_by", ctx="guardrails.metadata")
    _require_str(metadata, "template_version", ctx="guardrails.metadata")


def validate_lock_config(lock_data: Dict[str, Any], *, expected_managed_files: Iterable[str]) -> None:
    _require_str(lock_data, "schema_version", ctx="lock")
    template_set = _require_str(lock_data, "template_set", ctx="lock")
    if template_set != "repo_ready":
        raise PlatformError(
            "lock.template_set must be 'repo_ready'",
            code="E_REPO_READY_INVALID_VALUE",
            reason="lock.template_set",
        )
    _require_str(lock_data, "template_version", ctx="lock")
    _require_str(lock_data, "applied_at", ctx="lock")
    managed_files = _require_list_of_str(lock_data, "managed_files", ctx="lock")

    expected = set(expected_managed_files)
    if not expected.issubset(set(managed_files)):
        raise PlatformError(
            "lock.managed_files is missing one or more required Phase 1 files",
            code="E_REPO_READY_INVALID_VALUE",
            reason="lock.managed_files",
        )

    _require_list_of_str(lock_data, "approved_by", ctx="lock")

    known_bad = _require_mapping(lock_data, "known_bad", ctx="lock")
    _require_list_of_str(known_bad, "skills", ctx="lock.known_bad")
    _require_list_of_str(known_bad, "mcp_servers", ctx="lock.known_bad")


def validate_runbook_config(runbook: Dict[str, Any]) -> None:
    _require_str(runbook, "schema_version", ctx="runbook")

    commands = _require_mapping(runbook, "commands", ctx="runbook")
    for key in ("build", "test", "lint", "format", "start", "dev"):
        _validate_command_entries(commands, key, ctx="runbook.commands")

    _require_list_of_str(runbook, "entrypoints", ctx="runbook")
    _validate_services(runbook)
    _require_list_of_str(runbook, "env_vars", ctx="runbook")

    notes = _require_mapping(runbook, "notes", ctx="runbook")
    _require_str(notes, "status", ctx="runbook.notes")

    metadata = _require_mapping(runbook, "metadata", ctx="runbook")
    _require_str(metadata, "managed_by", ctx="runbook.metadata")
    _require_str(metadata, "template_version", ctx="runbook.metadata")
