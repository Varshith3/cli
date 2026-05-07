from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.tools.git_repo import get_current_branch

JENKINSFILE_REL_PATH = "Jenkinsfile"
JENKINS_CONTRACT_REL_PATH = ".ghdp/ci/jenkins_contract.json"
JENKINS_CONTRACT_SCHEMA_VERSION = "2.0"
_SUPPORTED_JENKINS_CONTRACT_SCHEMA_VERSIONS = {"1.0", "2.0"}
_RELEASE_POLICY_VERSION = "2026-04-16"
_PARITY_FIXTURE_ROOT = "platform-cli/tests/fixtures/release_migration/v1"
_LOCAL_DIAGNOSTICS_ROOT = "~/.ghdp/release/diagnostics"
_SHARED_RELEASE_FLOWS = {
    "feature_to_dev": {
        "index": 1,
        "display_label": "feature-to-dev",
        "execution_backend": "jenkins_api",
        "job_path": "job/UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev",
        "job_repository": "dp-tools-release-management",
        "job_branch": "1-promote-feature-to-dev",
        "trigger_endpoint": "buildWithParameters",
        "queue_timeout_s": 180,
        "build_timeout_s": 900,
        "poll_interval_s": 2,
        "expected_terminal_states": ["SUCCESS", "FAILURE", "ABORTED"],
        "artifact_expectations": [],
        "branch_rules": {"prefix": "feature/"},
        "parameter_names": ["GITHUB_TOKEN", "REPOSITORY", "FEATURE_BRANCH_NAME", "DEPLOY_ON_SQA"],
        "result_strategy": "github_cli_then_console",
    },
    "make_release": {
        "index": 2,
        "display_label": "make-release",
        "execution_backend": "jenkins_api",
        "job_path": "job/UDP/job/github-tools/job/dp-tools-release-management/job/2-make-release",
        "job_repository": "dp-tools-release-management",
        "job_branch": "2-make-release",
        "trigger_endpoint": "buildWithParameters",
        "queue_timeout_s": 180,
        "build_timeout_s": 900,
        "poll_interval_s": 2,
        "expected_terminal_states": ["SUCCESS", "FAILURE", "ABORTED"],
        "artifact_expectations": [],
        "branch_rules": {"source_branch": "develop"},
        "parameter_names": [
            "PARENT",
            "RELEASE_TYPE",
            "REPOSITORY",
            "SOURCE_BRANCH",
            "APPLY",
            "TESTED_OK_ON_UAT",
        ],
        "result_strategy": "build_url_only",
    },
}
_RECOGNIZED_HELPERS = (
    "downloadDependencies",
    "initWorkspace",
    "initWorkspaceUpgrade",
    "validateAndPlan",
    "terraformApply",
    "dockerBuild",
    "mavenBuild",
    "pushLogsAndMetadata",
)
_PARAM_KIND_BY_CALL = {
    "booleanParam": "boolean",
    "choice": "choice",
    "string": "string",
    "text": "text",
}


@dataclass(frozen=True)
class JenkinsParameter:
    name: str
    kind: str
    required: bool
    description: str = ""
    default: Any = None
    default_source: str = ""
    choices: list[str] = field(default_factory=list)
    choices_source: str = ""
    source_call: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JenkinsContractInspection:
    rel_path: str
    abs_path: str
    exists: bool
    fresh: bool
    jenkinsfile_exists: bool
    pipeline_style: str
    branch_name: str
    source_hash: str = ""
    recorded_hash: str = ""
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JenkinsContractSyncResult:
    rel_path: str
    abs_path: str
    status: str
    message: str
    branch_name: str
    source_hash: str = ""


def resolve_repo_root(explicit_repo_root: Path | None = None) -> Path:
    if explicit_repo_root is not None:
        root = explicit_repo_root.expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise PlatformError(
                f"Invalid repo root: {root}",
                code="E_REPO_ROOT_NOT_FOUND",
                reason=str(root),
            )
        return root

    result = run_cmd(["git", "rev-parse", "--show-toplevel"], check=False, cwd=Path.cwd())
    if result.returncode != 0 or not result.stdout:
        raise PlatformError(
            "Could not determine the repository root. Run this command inside a git repository or pass --repo-root.",
            code="E_REPO_ROOT_NOT_FOUND",
            reason="git",
        )

    repo_root = Path(result.stdout.strip()).resolve()
    if not repo_root.exists():
        raise PlatformError(
            f"Resolved repo root does not exist: {repo_root}",
            code="E_REPO_ROOT_NOT_FOUND",
            reason=str(repo_root),
        )
    return repo_root


def inspect_repo_jenkins_contract(repo_root: Path) -> JenkinsContractInspection:
    resolved_root = resolve_repo_root(repo_root)
    jenkinsfile_path = resolved_root / JENKINSFILE_REL_PATH
    contract_path = resolved_root / JENKINS_CONTRACT_REL_PATH
    branch_name = get_current_branch(resolved_root)
    pipeline_style = "missing"

    if not jenkinsfile_path.exists():
        return JenkinsContractInspection(
            rel_path=JENKINS_CONTRACT_REL_PATH,
            abs_path=str(contract_path),
            exists=contract_path.exists(),
            fresh=False,
            jenkinsfile_exists=False,
            pipeline_style=pipeline_style,
            branch_name=branch_name,
            messages=[],
        )

    jenkins_text = jenkinsfile_path.read_text(encoding="utf-8")
    source_hash = _sha256_text(jenkins_text)
    pipeline_style = _detect_pipeline_style(jenkins_text)

    if not contract_path.exists():
        return JenkinsContractInspection(
            rel_path=JENKINS_CONTRACT_REL_PATH,
            abs_path=str(contract_path),
            exists=False,
            fresh=False,
            jenkinsfile_exists=True,
            pipeline_style=pipeline_style,
            branch_name=branch_name,
            source_hash=source_hash,
            messages=[f"Missing {JENKINS_CONTRACT_REL_PATH}; run `ghdp repo ready --fix-jenkins-contract`."],
        )

    try:
        payload = _load_contract_payload(contract_path)
    except PlatformError as exc:
        return JenkinsContractInspection(
            rel_path=JENKINS_CONTRACT_REL_PATH,
            abs_path=str(contract_path),
            exists=True,
            fresh=False,
            jenkinsfile_exists=True,
            pipeline_style=pipeline_style,
            branch_name=branch_name,
            source_hash=source_hash,
            messages=[str(exc)],
        )

    recorded_hash = _string_field(payload.get("source", {}), "jenkinsfile_hash")
    fresh = recorded_hash == source_hash and bool(recorded_hash)
    messages: list[str] = []
    if not fresh:
        messages.append(
            "Jenkins contract is stale compared with the current Jenkinsfile. Run `ghdp repo ready --refresh-jenkins-contract`."
        )

    return JenkinsContractInspection(
        rel_path=JENKINS_CONTRACT_REL_PATH,
        abs_path=str(contract_path),
        exists=True,
        fresh=fresh,
        jenkinsfile_exists=True,
        pipeline_style=pipeline_style,
        branch_name=branch_name,
        source_hash=source_hash,
        recorded_hash=recorded_hash,
        messages=messages,
    )


def ensure_repo_jenkins_contract(repo_root: Path, *, refresh: bool) -> JenkinsContractSyncResult:
    resolved_root = resolve_repo_root(repo_root)
    jenkinsfile_path = resolved_root / JENKINSFILE_REL_PATH
    contract_path = resolved_root / JENKINS_CONTRACT_REL_PATH
    branch_name = get_current_branch(resolved_root)

    if not jenkinsfile_path.exists():
        raise PlatformError(
            f"Could not find {JENKINSFILE_REL_PATH} in {resolved_root}.",
            code="E_REPO_JENKINSFILE_MISSING",
            reason=JENKINSFILE_REL_PATH,
        )

    jenkins_text = jenkinsfile_path.read_text(encoding="utf-8")
    source_hash = _sha256_text(jenkins_text)

    if not refresh and contract_path.exists():
        try:
            payload = _load_contract_payload(contract_path)
        except PlatformError:
            payload = {}
        recorded_hash = _string_field(payload.get("source", {}), "jenkinsfile_hash")
        if recorded_hash == source_hash:
            return JenkinsContractSyncResult(
                rel_path=JENKINS_CONTRACT_REL_PATH,
                abs_path=str(contract_path),
                status="unchanged",
                message="Jenkins contract is already current.",
                branch_name=branch_name,
                source_hash=source_hash,
            )

    payload = build_repo_jenkins_contract(repo_root=resolved_root, branch_name=branch_name, jenkins_text=jenkins_text)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    previous = contract_path.read_text(encoding="utf-8") if contract_path.exists() else None
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    contract_path.write_text(rendered, encoding="utf-8")

    if previous is None:
        status = "created"
        message = "Created Jenkins contract from Jenkinsfile."
    elif previous == rendered:
        status = "unchanged"
        message = "Jenkins contract is already current."
    else:
        status = "updated"
        message = "Refreshed Jenkins contract from Jenkinsfile."

    return JenkinsContractSyncResult(
        rel_path=JENKINS_CONTRACT_REL_PATH,
        abs_path=str(contract_path),
        status=status,
        message=message,
        branch_name=branch_name,
        source_hash=source_hash,
    )


def load_repo_jenkins_contract(repo_root: Path) -> dict[str, Any]:
    resolved_root = resolve_repo_root(repo_root)
    contract_path = resolved_root / JENKINS_CONTRACT_REL_PATH
    return _load_contract_payload(contract_path)


def build_repo_jenkins_contract(*, repo_root: Path, branch_name: str, jenkins_text: str) -> dict[str, Any]:
    parameters = [item.to_dict() for item in _extract_jenkins_parameters(jenkins_text)]
    pipeline_style = _detect_pipeline_style(jenkins_text)
    helpers = sorted({helper for helper in _RECOGNIZED_HELPERS if re.search(rf"\b{re.escape(helper)}\s*\(", jenkins_text)})
    stages = re.findall(r"""stage\s*\(\s*["']([^"']+)["']\s*\)""", jenkins_text)

    return {
        "schema_version": JENKINS_CONTRACT_SCHEMA_VERSION,
        "policy_version": _RELEASE_POLICY_VERSION,
        "generated_by": "ghdp",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_name": repo_root.name,
        "source": {
            "branch_name": branch_name,
            "jenkinsfile_path": JENKINSFILE_REL_PATH,
            "jenkinsfile_hash": _sha256_text(jenkins_text),
        },
        "pipeline": {
            "style": pipeline_style,
            "stage_names": stages,
            "helper_calls": helpers,
            "uses_ghdp_commands": pipeline_style == "ghdp_native_pipeline",
        },
        "release_surface": {
            "selection_style": "indexed",
            "indexed_choices": [
                {
                    "index": _SHARED_RELEASE_FLOWS["feature_to_dev"]["index"],
                    "flow": "feature_to_dev",
                    "label": _SHARED_RELEASE_FLOWS["feature_to_dev"]["display_label"],
                    "selectable": True,
                },
                {
                    "index": _SHARED_RELEASE_FLOWS["make_release"]["index"],
                    "flow": "make_release",
                    "label": _SHARED_RELEASE_FLOWS["make_release"]["display_label"],
                    "selectable": True,
                },
            ],
        },
        "auth": {
            "resolution_order": ["cli", "env", "config", "prompt"],
            "supports_saved_jenkins_token": True,
            "env_keys": {
                "email": ["OKTA_USER_EMAIL", "GHDP_JENKINS_EMAIL"],
                "jenkins_token": ["JENKINS_API_TOKEN", "GHDP_JENKINS_API_TOKEN", "GHDP_JENKINS_TOKEN"],
                "github_token": ["GITHUB_API_TOKEN", "GHDP_GITHUB_API_TOKEN"],
            },
            "config_keys": {
                "email": "jenkins.okta_email",
                "jenkins_token": "jenkins.api_token",
            },
            "non_interactive": {
                "missing_auth_code": "E_RELEASE_JENKINS_TOKEN_MISSING",
                "invalid_auth_code": "E_RELEASE_JENKINS_TOKEN_INVALID",
            },
            "reviewable_secret_storage": False,
            "local_user_config_secret_storage": True,
        },
        "comparison": {
            "fixture_root": _PARITY_FIXTURE_ROOT,
            "normalized_fields": ["flow", "status", "build_number", "pull_request_url", "diagnostic_reason"],
            "ignore_fields": ["message", "build_url", "console_url", "timestamp", "queue_url"],
            "local_diagnostics_root": _LOCAL_DIAGNOSTICS_ROOT,
        },
        "redaction": {
            "sensitive_fields": [
                "JENKINS_API_TOKEN",
                "GHDP_JENKINS_API_TOKEN",
                "GHDP_JENKINS_TOKEN",
                "GITHUB_API_TOKEN",
                "GHDP_GITHUB_API_TOKEN",
                "crumb",
                "Authorization",
            ]
        },
        "routing": {
            "mcp_server": "dpe-jenkins",
            "feature_to_dev_tool": "create_pull_request",
            "make_release_tool": "create_release",
            "develop_build_tool": "deploy",
        },
        "flows": {
            "feature_to_dev": {
                "supported": True,
                "mode": "shared_release_management",
                "default_branch_prefix": "feature/",
                "index": _SHARED_RELEASE_FLOWS["feature_to_dev"]["index"],
                "display_label": _SHARED_RELEASE_FLOWS["feature_to_dev"]["display_label"],
                "execution_backend": _SHARED_RELEASE_FLOWS["feature_to_dev"]["execution_backend"],
                "job_path": _SHARED_RELEASE_FLOWS["feature_to_dev"]["job_path"],
                "job_repository": _SHARED_RELEASE_FLOWS["feature_to_dev"]["job_repository"],
                "job_branch": _SHARED_RELEASE_FLOWS["feature_to_dev"]["job_branch"],
                "trigger_endpoint": _SHARED_RELEASE_FLOWS["feature_to_dev"]["trigger_endpoint"],
                "queue_timeout_s": _SHARED_RELEASE_FLOWS["feature_to_dev"]["queue_timeout_s"],
                "build_timeout_s": _SHARED_RELEASE_FLOWS["feature_to_dev"]["build_timeout_s"],
                "poll_interval_s": _SHARED_RELEASE_FLOWS["feature_to_dev"]["poll_interval_s"],
                "expected_terminal_states": _SHARED_RELEASE_FLOWS["feature_to_dev"]["expected_terminal_states"],
                "artifact_expectations": _SHARED_RELEASE_FLOWS["feature_to_dev"]["artifact_expectations"],
                "result_strategy": _SHARED_RELEASE_FLOWS["feature_to_dev"]["result_strategy"],
                "parameter_names": _SHARED_RELEASE_FLOWS["feature_to_dev"]["parameter_names"],
            },
            "make_release": {
                "supported": True,
                "mode": "shared_release_management",
                "dynamic_params_from_jenkinsfile": bool(parameters),
                "index": _SHARED_RELEASE_FLOWS["make_release"]["index"],
                "display_label": _SHARED_RELEASE_FLOWS["make_release"]["display_label"],
                "execution_backend": _SHARED_RELEASE_FLOWS["make_release"]["execution_backend"],
                "job_path": _SHARED_RELEASE_FLOWS["make_release"]["job_path"],
                "job_repository": _SHARED_RELEASE_FLOWS["make_release"]["job_repository"],
                "job_branch": _SHARED_RELEASE_FLOWS["make_release"]["job_branch"],
                "trigger_endpoint": _SHARED_RELEASE_FLOWS["make_release"]["trigger_endpoint"],
                "queue_timeout_s": _SHARED_RELEASE_FLOWS["make_release"]["queue_timeout_s"],
                "build_timeout_s": _SHARED_RELEASE_FLOWS["make_release"]["build_timeout_s"],
                "poll_interval_s": _SHARED_RELEASE_FLOWS["make_release"]["poll_interval_s"],
                "expected_terminal_states": _SHARED_RELEASE_FLOWS["make_release"]["expected_terminal_states"],
                "artifact_expectations": _SHARED_RELEASE_FLOWS["make_release"]["artifact_expectations"],
                "result_strategy": _SHARED_RELEASE_FLOWS["make_release"]["result_strategy"],
                "parameter_names": _SHARED_RELEASE_FLOWS["make_release"]["parameter_names"],
            },
            "develop_build": {
                "supported": bool(parameters),
                "mode": pipeline_style,
                "default_branch": "develop",
                "confirm_gate_supported": True,
                "selectable": False,
                "display_label": "develop-build",
                "execution_backend": "contract_only",
            },
        },
        "parameter_schema": parameters,
    }


def _load_contract_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PlatformError(
            f"Repo Jenkins contract not found: {path}",
            code="E_REPO_JENKINS_CONTRACT_MISSING",
            reason=str(path),
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlatformError(
            f"Invalid JSON in {path}: {exc}",
            code="E_REPO_JENKINS_CONTRACT_INVALID",
            reason=str(path),
        )

    if not isinstance(payload, dict):
        raise PlatformError(
            f"Expected a JSON object in {path}.",
            code="E_REPO_JENKINS_CONTRACT_INVALID",
            reason=str(path),
        )

    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version not in _SUPPORTED_JENKINS_CONTRACT_SCHEMA_VERSIONS:
        raise PlatformError(
            f"Unsupported Jenkins contract schema '{schema_version or '(missing)'}' in {path}.",
            code="E_REPO_JENKINS_CONTRACT_INVALID",
            reason="schema_version",
        )

    return _upgrade_contract_payload(payload)


def _upgrade_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version == JENKINS_CONTRACT_SCHEMA_VERSION:
        return payload

    upgraded = dict(payload)
    source = dict(upgraded.get("source") or {})
    repo_name = str(upgraded.get("repo_name") or "").strip()
    generated_at = str(upgraded.get("generated_at") or "").strip()
    generated_by = str(upgraded.get("generated_by") or "ghdp").strip()
    pipeline = dict(upgraded.get("pipeline") or {})
    parameter_schema = list(upgraded.get("parameter_schema") or [])
    flows = dict(upgraded.get("flows") or {})
    feature_flow = dict(flows.get("feature_to_dev") or {})
    make_flow = dict(flows.get("make_release") or {})
    develop_flow = dict(flows.get("develop_build") or {})

    return {
        "schema_version": JENKINS_CONTRACT_SCHEMA_VERSION,
        "policy_version": _RELEASE_POLICY_VERSION,
        "generated_by": generated_by,
        "generated_at": generated_at,
        "repo_name": repo_name,
        "source": source,
        "pipeline": pipeline,
        "release_surface": {
            "selection_style": "indexed",
            "indexed_choices": [
                {"index": 1, "flow": "feature_to_dev", "label": "feature-to-dev", "selectable": True},
                {"index": 2, "flow": "make_release", "label": "make-release", "selectable": True},
            ],
        },
        "auth": {
            "resolution_order": ["cli", "env", "config", "prompt"],
            "supports_saved_jenkins_token": True,
            "env_keys": {
                "email": ["OKTA_USER_EMAIL", "GHDP_JENKINS_EMAIL"],
                "jenkins_token": ["JENKINS_API_TOKEN", "GHDP_JENKINS_API_TOKEN", "GHDP_JENKINS_TOKEN"],
                "github_token": ["GITHUB_API_TOKEN", "GHDP_GITHUB_API_TOKEN"],
            },
            "config_keys": {"email": "jenkins.okta_email", "jenkins_token": "jenkins.api_token"},
            "non_interactive": {
                "missing_auth_code": "E_RELEASE_JENKINS_TOKEN_MISSING",
                "invalid_auth_code": "E_RELEASE_JENKINS_TOKEN_INVALID",
            },
            "reviewable_secret_storage": False,
            "local_user_config_secret_storage": True,
        },
        "comparison": {
            "fixture_root": _PARITY_FIXTURE_ROOT,
            "normalized_fields": ["flow", "status", "build_number", "pull_request_url", "diagnostic_reason"],
            "ignore_fields": ["message", "build_url", "console_url", "timestamp", "queue_url"],
            "local_diagnostics_root": _LOCAL_DIAGNOSTICS_ROOT,
        },
        "redaction": {
            "sensitive_fields": [
                "JENKINS_API_TOKEN",
                "GHDP_JENKINS_API_TOKEN",
                "GHDP_JENKINS_TOKEN",
                "GITHUB_API_TOKEN",
                "GHDP_GITHUB_API_TOKEN",
                "crumb",
                "Authorization",
            ]
        },
        "routing": dict(upgraded.get("routing") or {}),
        "flows": {
            "feature_to_dev": {
                "supported": bool(feature_flow.get("supported", True)),
                "mode": str(feature_flow.get("mode") or "shared_release_management"),
                "default_branch_prefix": str(feature_flow.get("default_branch_prefix") or "feature/"),
                "index": _SHARED_RELEASE_FLOWS["feature_to_dev"]["index"],
                "display_label": _SHARED_RELEASE_FLOWS["feature_to_dev"]["display_label"],
                "execution_backend": _SHARED_RELEASE_FLOWS["feature_to_dev"]["execution_backend"],
                "job_path": _SHARED_RELEASE_FLOWS["feature_to_dev"]["job_path"],
                "job_repository": _SHARED_RELEASE_FLOWS["feature_to_dev"]["job_repository"],
                "job_branch": _SHARED_RELEASE_FLOWS["feature_to_dev"]["job_branch"],
                "trigger_endpoint": _SHARED_RELEASE_FLOWS["feature_to_dev"]["trigger_endpoint"],
                "queue_timeout_s": _SHARED_RELEASE_FLOWS["feature_to_dev"]["queue_timeout_s"],
                "build_timeout_s": _SHARED_RELEASE_FLOWS["feature_to_dev"]["build_timeout_s"],
                "poll_interval_s": _SHARED_RELEASE_FLOWS["feature_to_dev"]["poll_interval_s"],
                "expected_terminal_states": _SHARED_RELEASE_FLOWS["feature_to_dev"]["expected_terminal_states"],
                "artifact_expectations": _SHARED_RELEASE_FLOWS["feature_to_dev"]["artifact_expectations"],
                "result_strategy": _SHARED_RELEASE_FLOWS["feature_to_dev"]["result_strategy"],
                "parameter_names": _SHARED_RELEASE_FLOWS["feature_to_dev"]["parameter_names"],
            },
            "make_release": {
                "supported": bool(make_flow.get("supported", True)),
                "mode": str(make_flow.get("mode") or "shared_release_management"),
                "dynamic_params_from_jenkinsfile": bool(make_flow.get("dynamic_params_from_jenkinsfile", bool(parameter_schema))),
                "index": _SHARED_RELEASE_FLOWS["make_release"]["index"],
                "display_label": _SHARED_RELEASE_FLOWS["make_release"]["display_label"],
                "execution_backend": _SHARED_RELEASE_FLOWS["make_release"]["execution_backend"],
                "job_path": _SHARED_RELEASE_FLOWS["make_release"]["job_path"],
                "job_repository": _SHARED_RELEASE_FLOWS["make_release"]["job_repository"],
                "job_branch": _SHARED_RELEASE_FLOWS["make_release"]["job_branch"],
                "trigger_endpoint": _SHARED_RELEASE_FLOWS["make_release"]["trigger_endpoint"],
                "queue_timeout_s": _SHARED_RELEASE_FLOWS["make_release"]["queue_timeout_s"],
                "build_timeout_s": _SHARED_RELEASE_FLOWS["make_release"]["build_timeout_s"],
                "poll_interval_s": _SHARED_RELEASE_FLOWS["make_release"]["poll_interval_s"],
                "expected_terminal_states": _SHARED_RELEASE_FLOWS["make_release"]["expected_terminal_states"],
                "artifact_expectations": _SHARED_RELEASE_FLOWS["make_release"]["artifact_expectations"],
                "result_strategy": _SHARED_RELEASE_FLOWS["make_release"]["result_strategy"],
                "parameter_names": _SHARED_RELEASE_FLOWS["make_release"]["parameter_names"],
            },
            "develop_build": {
                "supported": bool(develop_flow.get("supported", bool(parameter_schema))),
                "mode": str(develop_flow.get("mode") or pipeline.get("style") or "missing"),
                "default_branch": str(develop_flow.get("default_branch") or "develop"),
                "confirm_gate_supported": bool(develop_flow.get("confirm_gate_supported", True)),
                "selectable": bool(develop_flow.get("selectable", False)),
                "display_label": str(develop_flow.get("display_label") or "develop-build"),
                "execution_backend": str(develop_flow.get("execution_backend") or "contract_only"),
            },
        },
        "parameter_schema": parameter_schema,
    }


def _extract_jenkins_parameters(text: str) -> list[JenkinsParameter]:
    block = _extract_named_block(text, "parameters")
    if not block:
        return []

    parameters: list[JenkinsParameter] = []
    for expression in _split_parameter_calls(block):
        parameter = _parse_parameter_call(expression)
        if parameter is not None:
            parameters.append(parameter)
    return parameters


def _extract_named_block(text: str, block_name: str) -> str:
    match = re.search(rf"\b{re.escape(block_name)}\s*\{{", text)
    if not match:
        return ""

    start = match.end() - 1
    depth = 0
    in_quote: str | None = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_quote:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == in_quote:
                in_quote = None
            continue

        if char in {"'", '"'}:
            in_quote = char
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    return ""


def _split_parameter_calls(block: str) -> list[str]:
    entries: list[str] = []
    index = 0
    while index < len(block):
        while index < len(block) and block[index] in " \t\r\n,":
            index += 1
        if index >= len(block):
            break

        identifier_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", block[index:])
        if not identifier_match:
            index += 1
            continue

        call_name = identifier_match.group(0)
        cursor = index + len(call_name)
        while cursor < len(block) and block[cursor].isspace():
            cursor += 1
        if cursor >= len(block) or block[cursor] != "(":
            index = cursor
            continue

        start = index
        depth = 0
        in_quote: str | None = None
        escaped = False
        while cursor < len(block):
            char = block[cursor]
            if in_quote:
                if escaped:
                    escaped = False
                    cursor += 1
                    continue
                if char == "\\":
                    escaped = True
                    cursor += 1
                    continue
                if char == in_quote:
                    in_quote = None
                cursor += 1
                continue

            if char in {"'", '"'}:
                in_quote = char
                cursor += 1
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            cursor += 1

        entries.append(block[start:cursor].strip())
        index = cursor
    return entries


def _parse_parameter_call(expression: str) -> JenkinsParameter | None:
    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", expression)
    if not match:
        return None

    call_name = match.group(1)
    kind = _PARAM_KIND_BY_CALL.get(call_name)
    if not kind:
        return None

    arguments = _parse_named_arguments(expression)
    name = _unquote(arguments.get("name", ""))
    if not name:
        return None

    description = _unquote(arguments.get("description", ""))
    default_value, default_source = _normalize_default(arguments.get("defaultValue", ""))
    choices, choices_source = _normalize_choices(arguments.get("choices", ""))
    required = default_source == "" and kind == "choice"

    return JenkinsParameter(
        name=name,
        kind=kind,
        required=required,
        description=description,
        default=default_value,
        default_source=default_source,
        choices=choices,
        choices_source=choices_source,
        source_call=call_name,
    )


def _parse_named_arguments(expression: str) -> dict[str, str]:
    inner = expression[expression.find("(") + 1 : expression.rfind(")")]
    arguments: dict[str, str] = {}
    index = 0
    while index < len(inner):
        while index < len(inner) and inner[index] in " \t\r\n,":
            index += 1
        if index >= len(inner):
            break

        key_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", inner[index:])
        if not key_match:
            index += 1
            continue

        key = key_match.group(0)
        cursor = index + len(key)
        while cursor < len(inner) and inner[cursor].isspace():
            cursor += 1
        if cursor >= len(inner) or inner[cursor] != ":":
            index = cursor
            continue
        cursor += 1
        while cursor < len(inner) and inner[cursor].isspace():
            cursor += 1

        value_start = cursor
        paren_depth = 0
        bracket_depth = 0
        brace_depth = 0
        in_quote: str | None = None
        escaped = False

        while cursor < len(inner):
            char = inner[cursor]
            if in_quote:
                if escaped:
                    escaped = False
                    cursor += 1
                    continue
                if char == "\\":
                    escaped = True
                    cursor += 1
                    continue
                if char == in_quote:
                    in_quote = None
                cursor += 1
                continue

            if char in {"'", '"'}:
                in_quote = char
                cursor += 1
                continue
            if char == "(":
                paren_depth += 1
            elif char == ")":
                if paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
                    break
                paren_depth -= 1
            elif char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth -= 1
            elif char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
            elif char == "," and paren_depth == 0 and bracket_depth == 0 and brace_depth == 0:
                break
            cursor += 1

        arguments[key] = inner[value_start:cursor].strip()
        if cursor < len(inner) and inner[cursor] == ",":
            cursor += 1
        index = cursor

    return arguments


def _normalize_default(raw: str) -> tuple[Any, str]:
    text = (raw or "").strip()
    if not text:
        return None, ""
    lowered = text.lower()
    if lowered == "true":
        return True, ""
    if lowered == "false":
        return False, ""
    if text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) >= 2:
        return _unquote(text), ""
    return None, text


def _normalize_choices(raw: str) -> tuple[list[str], str]:
    text = (raw or "").strip()
    if not text:
        return [], ""
    if text.startswith("[") and text.endswith("]"):
        items = re.findall(r"""['"]([^'"]+)['"]""", text)
        return items, ""
    return [], text


def _unquote(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _string_field(payload: Any, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(key) or "").strip()


def _detect_pipeline_style(text: str) -> str:
    if "ghdp ci setup" in text or re.search(r"\bghdp\s+(build|publish|deploy)\b", text):
        return "ghdp_native_pipeline"
    return "repo_local_pipeline"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
