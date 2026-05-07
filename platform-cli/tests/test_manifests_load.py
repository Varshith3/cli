from __future__ import annotations

import json
from pathlib import Path

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.load import (
    load_claude_athena_workgroup_map,
    load_manifests,
    load_packaged_team_sync_policy_fallback,
    load_optional_team_policy,
    preferred_managed_claude_athena_workgroup_map_path,
    preferred_managed_toolset_path,
    preferred_legacy_toolset_path,
    preferred_user_claude_athena_workgroup_map_path,
    preferred_user_toolset_path,
    toolset_source_kind,
)
from platform_cli.manifests.validate import validate_toolset_ownership_alignment, validate_toolset_policy_source


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _toolset_payload(version: str) -> dict:
    return {
        "schema_version": "0.0.1",
        "teams": {
            "platform": {
                "tools": {
                    "gh": {
                        "op": ">=",
                        "version": version,
                    }
                }
            }
        },
    }


def _toolset_ownership_payload(version: str, *, allow_user_override: bool) -> dict:
    payload = _toolset_payload(version)
    payload["teams"]["platform"]["tools"]["gh"]["ownership"] = {
        "default_owner": "ghdp",
        "allow_user_override": allow_user_override,
    }
    return payload


def test_toolset_paths_keep_user_override_and_managed_sync_separate(isolated_home):
    user_path = preferred_user_toolset_path()
    legacy_user_path = preferred_legacy_toolset_path()
    managed_path = preferred_managed_toolset_path()

    assert user_path.parent.name == "manifests"
    assert user_path.name == "toolset.json"
    assert legacy_user_path.parent.name == ".ghdp"
    assert legacy_user_path.name == "toolset.json"
    assert managed_path.parent.name == "policies"
    assert managed_path.name == "team-toolset.managed.json"
    assert managed_path != user_path
    assert managed_path != legacy_user_path


def test_toolset_source_kind_labels_managed_user_and_packaged():
    assert toolset_source_kind("managed:/tmp/team-toolset.managed.json") == "managed"
    assert toolset_source_kind("user:/tmp/toolset.json") == "user"
    assert toolset_source_kind("env:GHDP_TOOLSET_JSON_PATH:/tmp/toolset.json") == "env"
    assert toolset_source_kind("pkg:platform_cli/resources/manifests/toolset.json") == "packaged"
    assert toolset_source_kind("cwd:/tmp/resources/toolset.json") == "dev"


def test_validate_toolset_policy_source_only_accepts_trusted_ownership_sources() -> None:
    validate_toolset_policy_source("managed:/tmp/team-toolset.managed.json")
    validate_toolset_policy_source("pkg:platform_cli/resources/manifests/toolset.json")

    with pytest.raises(PlatformError) as exc:
        validate_toolset_policy_source("user:/tmp/toolset.json")

    assert exc.value.code == "E_MANIFEST_INVALID"
    assert "source 'user:/tmp/toolset.json' is not trusted" in str(exc.value)


def test_validate_toolset_ownership_alignment_rejects_drift() -> None:
    packaged_toolset = _toolset_ownership_payload("1.0.0", allow_user_override=False)
    managed_toolset = _toolset_ownership_payload("2.0.0", allow_user_override=False)
    managed_toolset["teams"]["platform"]["tools"]["gh"]["ownership"]["allow_user_override"] = True

    with pytest.raises(PlatformError) as exc:
        validate_toolset_ownership_alignment(packaged_toolset, managed_toolset)

    assert exc.value.code == "E_MANIFEST_INVALID"
    assert exc.value.reason == "toolset:ownership_alignment"


def test_load_manifests_prefers_managed_toolset_over_packaged_fallback(isolated_home):
    managed_payload = _toolset_payload("2.0.0")
    _write_json(preferred_managed_toolset_path(), managed_payload)

    toolset, registry, sources = load_manifests()

    assert toolset == managed_payload
    assert toolset_source_kind(sources["toolset"]) == "managed"
    assert registry["schema_version"]


@pytest.mark.parametrize(
    "user_path_factory",
    [
        lambda: preferred_user_toolset_path(),
        lambda: preferred_legacy_toolset_path(),
    ],
    ids=["new-user-path", "legacy-user-path"],
)
def test_load_manifests_prefers_user_override_over_managed_sync(isolated_home, user_path_factory):
    user_payload = _toolset_payload("1.0.0")
    managed_payload = _toolset_payload("2.0.0")

    _write_json(user_path_factory(), user_payload)
    _write_json(preferred_managed_toolset_path(), managed_payload)

    toolset, _, sources = load_manifests()

    assert toolset == user_payload
    assert toolset_source_kind(sources["toolset"]) == "user"


def test_load_manifests_falls_back_to_packaged_toolset_when_no_local_sources_exist(isolated_home):
    toolset, _, sources = load_manifests()

    assert toolset["schema_version"] == "0.0.1"
    assert toolset_source_kind(sources["toolset"]) == "packaged"


def test_load_optional_team_policy_falls_back_to_packaged_policy(isolated_home):
    policy, source = load_optional_team_policy()

    assert policy is not None
    assert "data_analyst" in policy.get("teams", {})
    assert "local.lifecycle" in policy["teams"]["data_analyst"]["deny_capabilities"]
    assert "publish.execute" in policy["teams"]["data_analyst"]["deny_capabilities"]
    assert source.startswith("pkg:")


def test_load_optional_team_policy_prefers_local_override_over_packaged_policy(isolated_home):
    local_policy = {
        "schema_version": "1.0",
        "admin_users": ["octocat"],
        "teams": {
            "platform": {
                "allow_capabilities": ["tableau.use"],
                "deny_capabilities": [],
            }
        },
    }
    _write_json(isolated_home / ".ghdp" / "policies" / "team-policy.managed.json", local_policy)

    policy, source = load_optional_team_policy()

    assert policy == local_policy
    assert source.startswith("user:")


def test_packaged_content_index_tracks_latest_admin_policy_bundle() -> None:
    index_path = Path(__file__).resolve().parents[1] / "release-assets" / "content_index" / "content-index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))

    admin_policy = next(item for item in payload["capabilities"] if item["capability"] == "ghdp-admin-policy")

    assert admin_policy["version"] == "1.1.8"
    assert admin_policy["tag"] == "ghdp-admin-policy-v1.1.8"


def _claude_mapping_payload(workgroup: str = "wg-derived") -> dict:
    return {
        "version": 1,
        "mappings": [
            {
                "account_id": "617336469044",
                "role_name": "dp-md-rwe-data-engineer",
                "athena_workgroup": workgroup,
            }
        ],
    }


def test_load_claude_athena_workgroup_map_prefers_user_managed_override(isolated_home):
    payload = _claude_mapping_payload("wg-user")
    _write_json(preferred_user_claude_athena_workgroup_map_path(), payload)

    mappings, source, fallback_active = load_claude_athena_workgroup_map()

    assert mappings == payload["mappings"]
    assert source.startswith("user:")
    assert fallback_active is False


def test_load_claude_athena_workgroup_map_prefers_env_override_over_local_sources(isolated_home, monkeypatch):
    user_payload = _claude_mapping_payload("wg-user")
    env_payload = _claude_mapping_payload("wg-env")
    env_path = isolated_home / "env-map.json"

    _write_json(preferred_user_claude_athena_workgroup_map_path(), user_payload)
    _write_json(env_path, env_payload)
    monkeypatch.setenv("GHDP_CLAUDE_ATHENA_WORKGROUP_MAP_PATH", str(env_path))

    mappings, source, fallback_active = load_claude_athena_workgroup_map()

    assert mappings == env_payload["mappings"]
    assert source.startswith("env:")
    assert fallback_active is False


def test_load_claude_athena_workgroup_map_rejects_invalid_user_override(isolated_home):
    path = preferred_user_claude_athena_workgroup_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1, "mappings": []}', encoding="utf-8")

    with pytest.raises(PlatformError) as exc:
        load_claude_athena_workgroup_map()

    assert exc.value.code == "E_MANIFEST_INVALID"
    assert "Invalid user-managed Claude Athena workgroup mapping" in str(exc.value)


def test_load_claude_athena_workgroup_map_prefers_managed_override(isolated_home):
    payload = _claude_mapping_payload("wg-managed")
    _write_json(preferred_managed_claude_athena_workgroup_map_path(), payload)

    mappings, source, fallback_active = load_claude_athena_workgroup_map()

    assert mappings == payload["mappings"]
    assert source.startswith("managed:")
    assert fallback_active is False


def test_load_claude_athena_workgroup_map_rejects_missing_env_override(isolated_home, monkeypatch):
    missing = isolated_home / "missing-map.json"
    monkeypatch.setenv("GHDP_CLAUDE_ATHENA_WORKGROUP_MAP_PATH", str(missing))

    with pytest.raises(PlatformError) as exc:
        load_claude_athena_workgroup_map()

    assert exc.value.code == "E_MANIFEST_INVALID"
    assert "Invalid env override Claude Athena workgroup mapping" in str(exc.value)


def test_load_claude_athena_workgroup_map_falls_back_to_packaged_backup(monkeypatch, isolated_home):
    from platform_cli.core.errors import PlatformError as CorePlatformError

    payload = _claude_mapping_payload("wg-backup")

    def _fake_read(subdir: str, filename: str):
        if filename == "athena-workgroup-map.json":
            raise CorePlatformError("primary broken", code="E_MANIFEST_INVALID", reason="primary")
        return payload

    monkeypatch.setattr("platform_cli.manifests.load._read_packaged_resource", _fake_read)

    mappings, source, fallback_active = load_claude_athena_workgroup_map()

    assert mappings == payload["mappings"]
    assert source.endswith("athena-workgroup-map.backup.json")
    assert fallback_active is True


def test_packaged_claude_athena_mapping_primary_and_backup_match():
    repo_root = Path(__file__).resolve().parents[1]
    primary = json.loads(
        (repo_root / "src" / "platform_cli" / "resources" / "claude" / "athena-workgroup-map.json").read_text(
            encoding="utf-8"
        )
    )
    backup = json.loads(
        (
            repo_root
            / "src"
            / "platform_cli"
            / "resources"
            / "claude"
            / "athena-workgroup-map.backup.json"
        ).read_text(encoding="utf-8")
    )

    assert primary == backup


def test_load_packaged_team_sync_policy_fallback_exposes_first_release_rules() -> None:
    payload, source = load_packaged_team_sync_policy_fallback()

    assert payload is not None
    assert source.startswith("pkg:")
    teams = payload["teams"]
    assert teams["data_platform"]["sync"]["allow_capabilities"]
    assert teams["data_analyst"]["sync"]["allow_capabilities"] == ["tableau-athena-jars"]
