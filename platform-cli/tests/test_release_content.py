from __future__ import annotations

import importlib.util
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_cli.core import release_content
from platform_cli.core.errors import PlatformError
from platform_cli.core.sync_providers import NormalizedPackageManifest, register_provider_factory
from platform_cli.core.sync_targets import register_target_handler
from platform_cli.state.store import get_tool_state, update_tool_state


def _load_script_module(script_name: str):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(script_name.replace(".py", ""), script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_root_key(root_key: str) -> Path:
    if root_key == "test_root":
        return Path.home() / ".test-root"
    raise PlatformError("bad root", code="E_BAD_ARGS", reason=root_key)


def _build_manifest(
    version: str,
    files: list[dict[str, str]] | None = None,
    *,
    capability: str = "example-capability",
    target_root_key: str = "test_root",
    target_subdir: str = "bundle",
) -> dict[str, object]:
    return {
        "capability": capability,
        "version": version,
        "target_root_key": target_root_key,
        "target_subdir": target_subdir,
        "files": files
        or [
            {"asset_name": "a.txt", "target_path": "a.txt"},
            {"asset_name": "b.txt", "target_path": "nested/b.txt"},
        ],
    }


def _build_index(
    tag: str,
    version: str,
    *,
    capability: str = "example-capability",
    allow_install_if_missing: bool = False,
    recovery_hint: str = "",
    **_ignored: object,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "generated_at": "2026-03-18T13:25:00Z",
        "capabilities": [
            {
                "capability": capability,
                "version": version,
                "provider": "github_release",
                "source": {
                    "repo": "owner/repo",
                    "tag": tag,
                    "manifest_asset": "content-manifest.json",
                },
                "package_type": "file_bundle",
                "target_type": "filesystem",
                "policy": {
                    "allow_update_existing_files": True,
                    "allow_new_files_on_update": False,
                    "allow_install_if_missing": allow_install_if_missing,
                    "min_cli_version": "0.1.0",
                },
                "recovery_hint": recovery_hint,
            }
        ],
    }


def _install_assets(tag_to_manifest: dict[str, dict[str, object]], tag_to_assets: dict[str, dict[str, bytes]]):
    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        asset_name = cmd[cmd.index("--pattern") + 1]
        download_dir = Path(cmd[cmd.index("--dir") + 1])
        tag = cmd[3]
        if asset_name == "content-index.json":
            (download_dir / asset_name).write_text(json.dumps(_build_index("v2.0.0", "2.0.0"), indent=2), encoding="utf-8")
            return
        if asset_name == "content-manifest.json":
            (download_dir / asset_name).write_text(json.dumps(tag_to_manifest[tag], indent=2), encoding="utf-8")
            return
        asset_map = tag_to_assets[tag]
        if asset_name not in asset_map:
            raise PlatformError("missing asset", code="E_CMD_FAILED", reason=asset_name)
        (download_dir / asset_name).write_bytes(asset_map[asset_name])

    return _fake_run


def _install_assets_with_index(
    tag_to_manifest: dict[str, dict[str, object]],
    tag_to_assets: dict[str, dict[str, bytes]],
    index_payload: dict[str, object],
):
    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        asset_name = cmd[cmd.index("--pattern") + 1]
        download_dir = Path(cmd[cmd.index("--dir") + 1])
        tag = cmd[3]
        if asset_name == "content-index.json":
            (download_dir / asset_name).write_text(json.dumps(index_payload, indent=2), encoding="utf-8")
            return
        if asset_name == "content-manifest.json":
            (download_dir / asset_name).write_text(json.dumps(tag_to_manifest[tag], indent=2), encoding="utf-8")
            return
        asset_map = tag_to_assets[tag]
        if asset_name not in asset_map:
            raise PlatformError("missing asset", code="E_CMD_FAILED", reason=asset_name)
        (download_dir / asset_name).write_bytes(asset_map[asset_name])

    return _fake_run


def _write_marketplace_skill(repo_root: Path, rel_path: str, files: dict[str, str]) -> None:
    skill_root = repo_root / Path(rel_path)
    skill_root.mkdir(parents=True, exist_ok=True)
    for rel_file, contents in files.items():
        target = skill_root / Path(rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")


def _write_marketplace_plugin(repo_root: Path, plugin_name: str, skills: dict[str, dict[str, str]]) -> None:
    plugin_root = repo_root / "plugins" / plugin_name
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": plugin_name,
                "description": f"{plugin_name} plugin",
                "version": "1.0.0",
                "author": {"name": "Data Platform Team"},
                "category": "data-platform",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for skill_name, files in skills.items():
        _write_marketplace_skill(repo_root, f"plugins/{plugin_name}/skills/{skill_name}", files)


def _write_repo_marketplace_policy(
    repo_root: Path,
    *,
    targets: dict[str, dict[str, object]],
    repo: str = "gh-org-data-platform/gh-dp-data-platform-skill-marketplace",
    branch: str = "develop",
) -> None:
    policy_root = repo_root / ".ghdp"
    policy_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "sources": {
            "skill_marketplace": {
                "repo": repo,
                "repo_path": str(repo_root),
                "branch": branch,
                "targets": targets,
            }
        }
    }
    (policy_root / "capability-allowlist.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_repo_marketplace_policy_v2(
    repo_root: Path,
    *,
    target_entries: dict[str, list[dict[str, str]]],
    repo: str = "gh-org-data-platform/gh-dp-data-platform-skill-marketplace",
    branch: str = "develop",
) -> None:
    policy_root = repo_root / ".ghdp"
    policy_root.mkdir(parents=True, exist_ok=True)
    payload = _managed_allowlist_payload_v2(repo_path=repo_root, target_entries=target_entries, repo=repo, branch=branch)
    (policy_root / "capability-allowlist.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _managed_allowlist_index_entry(
    *,
    version: str = "1.2.0",
    tag: str = "marketplace-skill-allowlist-v1.2.0",
) -> dict[str, object]:
    return {
        "capability": "marketplace-skill-allowlist",
        "version": version,
        "provider": "github_release",
        "source": {
            "repo": "gh-org-data-platform/dp-tools-local-setup",
            "tag": tag,
            "manifest_asset": "content-manifest.json",
        },
        "package_type": "file_bundle",
        "target_type": "filesystem",
        "policy": {
            "allow_update_existing_files": True,
            "allow_new_files_on_update": False,
            "allow_install_if_missing": True,
        },
    }


def _managed_team_policy_index_entry(
    *,
    version: str = "1.0.0",
    tag: str = "ghdp-team-policy-v1.0.0",
) -> dict[str, object]:
    return {
        "capability": "ghdp-team-policy",
        "version": version,
        "provider": "github_release",
        "source": {
            "repo": "gh-org-data-platform/dp-tools-local-setup",
            "tag": tag,
            "manifest_asset": "content-manifest.json",
        },
        "package_type": "file_bundle",
        "target_type": "filesystem",
        "policy": {
            "allow_update_existing_files": True,
            "allow_new_files_on_update": False,
            "allow_install_if_missing": True,
        },
    }


def _managed_allowlist_payload(*, repo_path: Path, targets: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "managed_by": "ghdp",
        "generated_at": "2026-03-25T00:00:00Z",
        "sources": {
            "skill_marketplace": {
                "repo": "gh-org-data-platform/gh-dp-data-platform-skill-marketplace",
                "repo_path": str(repo_path),
                "branch": "develop",
                "targets": targets,
            }
        },
    }


def _managed_team_policy_payload(*, teams: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "managed_by": "ghdp",
        "generated_at": "2026-03-27T00:00:00Z",
        "teams": teams,
    }


def _marketplace_entry(
    *,
    capability: str,
    install_unit_type: str,
    source_path: str,
    target_type: str,
    target_root_key: str,
    target_subdir: str,
    category: str,
) -> dict[str, str]:
    return {
        "capability": capability,
        "install_unit_type": install_unit_type,
        "source_path": source_path,
        "target_type": target_type,
        "target_root_key": target_root_key,
        "target_subdir": target_subdir,
        "category": category,
    }


def _managed_allowlist_payload_v2(
    *,
    repo_path: Path,
    target_entries: dict[str, list[dict[str, str]]],
    repo: str = "gh-org-data-platform/gh-dp-data-platform-skill-marketplace",
    branch: str = "develop",
) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "managed_by": "ghdp",
        "generated_at": "2026-03-27T00:00:00Z",
        "sources": {
            "skill_marketplace": {
                "repo": repo,
                "repo_path": str(repo_path),
                "branch": branch,
                "targets": {name: {"entries": list(entries)} for name, entries in target_entries.items()},
            }
        },
    }


def _marketplace_run(
    repo_path: Path,
    *,
    commit: str = "abc123def4567890",
    allowlist_payload: dict[str, object] | None = None,
    team_policy_payload: dict[str, object] | None = None,
    base_capabilities: list[dict[str, object]] | None = None,
) -> object:
    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        allowlist_entry = next(
            (item for item in (base_capabilities or []) if str(item.get("capability")) == "marketplace-skill-allowlist"),
            None,
        )
        team_policy_entry = next(
            (item for item in (base_capabilities or []) if str(item.get("capability")) == "ghdp-team-policy"),
            None,
        )
        allowlist_tag = "marketplace-skill-allowlist-v1.2.0"
        allowlist_version = "1.2.0"
        if allowlist_entry is not None:
            source = allowlist_entry.get("source")
            if isinstance(source, dict):
                allowlist_tag = str(source.get("tag", allowlist_tag)).strip() or allowlist_tag
            allowlist_version = str(allowlist_entry.get("version", allowlist_version)).strip() or allowlist_version
        team_policy_tag = "ghdp-team-policy-v1.0.0"
        team_policy_version = "1.0.0"
        if team_policy_entry is not None:
            source = team_policy_entry.get("source")
            if isinstance(source, dict):
                team_policy_tag = str(source.get("tag", team_policy_tag)).strip() or team_policy_tag
            team_policy_version = str(team_policy_entry.get("version", team_policy_version)).strip() or team_policy_version

        if cmd[:4] == ["gh", "release", "download", "content-index-latest"]:
            asset_name = cmd[cmd.index("--pattern") + 1]
            download_dir = Path(cmd[cmd.index("--dir") + 1])
            if asset_name != "content-index.json":
                raise PlatformError("unexpected asset", code="E_CMD_FAILED", reason=asset_name)
            payload = {
                "schema_version": "1.0",
                "generated_at": "2026-03-25T12:00:00Z",
                "capabilities": list(base_capabilities or []),
            }
            (download_dir / asset_name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return SimpleNamespace(stdout="")

        if cmd[:4] == ["gh", "release", "download", allowlist_tag]:
            asset_name = cmd[cmd.index("--pattern") + 1]
            download_dir = Path(cmd[cmd.index("--dir") + 1])
            if asset_name == "content-manifest.json":
                payload = {
                    "schema_version": "1.0",
                    "capability": "marketplace-skill-allowlist",
                    "version": allowlist_version,
                    "target_root_key": "ghdp_root",
                    "target_subdir": "policies",
                    "files": [
                        {
                            "asset_name": "capability-allowlist.managed.json",
                            "target_path": "capability-allowlist.managed.json",
                        }
                    ],
                }
                (download_dir / asset_name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return SimpleNamespace(stdout="")
            if asset_name == "capability-allowlist.managed.json" and allowlist_payload is not None:
                (download_dir / asset_name).write_text(json.dumps(allowlist_payload, indent=2), encoding="utf-8")
                return SimpleNamespace(stdout="")
            raise PlatformError("unexpected asset", code="E_CMD_FAILED", reason=asset_name)

        if cmd[:4] == ["gh", "release", "download", team_policy_tag]:
            asset_name = cmd[cmd.index("--pattern") + 1]
            download_dir = Path(cmd[cmd.index("--dir") + 1])
            if asset_name == "content-manifest.json":
                payload = {
                    "schema_version": "1.0",
                    "capability": "ghdp-team-policy",
                    "version": team_policy_version,
                    "target_root_key": "ghdp_root",
                    "target_subdir": "policies",
                    "files": [
                        {
                            "asset_name": "team-policy.managed.json",
                            "target_path": "team-policy.managed.json",
                        }
                    ],
                }
                (download_dir / asset_name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return SimpleNamespace(stdout="")
            if asset_name == "team-policy.managed.json" and team_policy_payload is not None:
                (download_dir / asset_name).write_text(json.dumps(team_policy_payload, indent=2), encoding="utf-8")
                return SimpleNamespace(stdout="")
            raise PlatformError("unexpected asset", code="E_CMD_FAILED", reason=asset_name)

        if cmd[:3] == ["git", "-C", str(repo_path)] and len(cmd) >= 5 and cmd[3] == "rev-parse":
            return SimpleNamespace(stdout=f"{commit}\n")

        if cmd[:3] == ["git", "-C", str(repo_path)] and len(cmd) >= 8 and cmd[3] == "archive":
            archive_path = Path(cmd[cmd.index("-o") + 1])
            with tarfile.open(archive_path, "w") as tar:
                for path in sorted(repo_path.rglob("*")):
                    if path.is_file():
                        tar.add(path, arcname=str(path.relative_to(repo_path)).replace("\\", "/"))
            return SimpleNamespace(stdout="")

        raise PlatformError(f"unexpected command: {cmd}", code="E_CMD_FAILED", reason="unexpected_command")

    return _fake_run


def _register_mock_provider(
    *,
    manifest: dict[str, object],
    assets: dict[str, bytes],
    provider_name: str = "mock_provider",
) -> str:
    class _MockProvider:
        name = provider_name

        def __init__(self, **_: object) -> None:
            pass

        def download_asset(self, *, source: dict[str, object], asset_name: str, download_dir: Path) -> Path:
            if asset_name not in assets:
                raise PlatformError("missing asset", code="E_CMD_FAILED", reason=asset_name)
            target = download_dir / asset_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(assets[asset_name])
            return target

        def load_package_manifest(self, *, source: dict[str, object]) -> NormalizedPackageManifest:
            return NormalizedPackageManifest(
                capability=str(manifest["capability"]),
                version=str(manifest["version"]),
                target_root_key=str(manifest["target_root_key"]),
                target_subdir=str(manifest["target_subdir"]),
                files=[dict(item) for item in manifest["files"]],  # type: ignore[index]
            )

    register_provider_factory(provider_name, lambda run_cmd_impl: _MockProvider())
    return provider_name


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_install_release_content_downloads_files_on_first_install(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        _install_assets(tag_to_manifest, tag_to_assets)(cmd, **kwargs)

    monkeypatch.setattr(release_content, "run_cmd", _fake_run)

    result = release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )

    target_root = isolated_home / ".test-root" / "bundle"
    assert result["source"] == "release"
    assert result["content_version"] == "1.0.0"
    assert result["updated_count"] == 2
    assert (target_root / "a.txt").read_bytes() == b"a"
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b"
    assert len(calls) == 3
    state = get_tool_state("content:example-capability")
    assert state["provider"] == "github_release"
    assert state["provider_source"]["repo"] == "owner/repo"


def test_install_release_content_skips_when_install_is_intact(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    count = {"calls": 0}

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        count["calls"] += 1
        _install_assets(tag_to_manifest, tag_to_assets)(cmd, **kwargs)

    monkeypatch.setattr(release_content, "run_cmd", _fake_run)

    first = release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )
    second = release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )

    assert first["source"] == "release"
    assert second["source"] == "existing"
    assert count["calls"] == 3


def test_install_release_content_repairs_missing_files(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )
    target_root = isolated_home / ".test-root" / "bundle"
    (target_root / "nested" / "b.txt").unlink()

    result = release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )

    assert result["source"] == "release"
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b"


def test_install_release_content_rejects_invalid_manifest(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": {"capability": "example-capability", "version": "1.0.0", "target_root_key": "test_root"}}
    tag_to_assets = {"v1.0.0": {}}
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    with pytest.raises(PlatformError) as err:
        release_content.install_release_content(
            capability="example-capability",
            repo="owner/repo",
            tag="v1.0.0",
            resolve_root_key=_resolve_root_key,
        )

    assert err.value.code == "E_RELEASE_CONTENT_MANIFEST_INVALID"


def test_preview_content_updates_uses_index_and_ignores_new_remote_files(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {
        "v1.0.0": _build_manifest("1.0.0"),
        "v2.0.0": _build_manifest(
            "2.0.0",
            files=[
                {"asset_name": "a.txt", "target_path": "a.txt"},
                {"asset_name": "b.txt", "target_path": "nested/b.txt"},
                {"asset_name": "c.txt", "target_path": "new/c.txt"},
            ],
        ),
    }
    tag_to_assets = {
        "v1.0.0": {"a.txt": b"a", "b.txt": b"b"},
        "v2.0.0": {"a.txt": b"a2", "b.txt": b"b2", "c.txt": b"c2"},
    }
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )

    preview = release_content.preview_content_updates()
    item = preview["capabilities"][0]

    assert item["action"] == "update"
    assert item["updatable_files"] == ["a.txt", "nested/b.txt"]
    assert item["ignored_new_files"] == ["new/c.txt"]


def test_preview_content_updates_marks_bootstrap_for_missing_install_when_allowed(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    index_payload = _build_index("v1.0.0", "1.0.0", allow_install_if_missing=True)
    monkeypatch.setattr(release_content, "run_cmd", _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload))

    preview = release_content.preview_content_updates(resolve_root_key=_resolve_root_key)
    item = preview["capabilities"][0]

    assert item["installed"] is False
    assert item["bootstrap_allowed"] is True
    assert item["recovery_mode"] == "bootstrap"
    assert item["action"] == "install"
    assert item["missing_local_files"] == ["a.txt", "nested/b.txt"]


def test_repair_content_bootstraps_missing_files_when_allowed(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    index_payload = _build_index("v1.0.0", "1.0.0", allow_install_if_missing=True)
    monkeypatch.setattr(release_content, "run_cmd", _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload))

    result = release_content.repair_content("example-capability", resolve_root_key=_resolve_root_key)
    target_root = isolated_home / ".test-root" / "bundle"

    assert result["local_status"] == "bootstrapped"
    assert result["repaired_count"] == 2
    assert (target_root / "a.txt").read_bytes() == b"a"
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b"
    state = get_tool_state("content:example-capability")
    assert state["source"] == "release"
    assert state["files"] == ["a.txt", "nested/b.txt"]


def test_repair_content_rejects_missing_install_when_bootstrap_is_disabled(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    index_payload = _build_index("v1.0.0", "1.0.0", allow_install_if_missing=False)
    monkeypatch.setattr(release_content, "run_cmd", _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload))

    with pytest.raises(PlatformError) as err:
        release_content.repair_content("example-capability", resolve_root_key=_resolve_root_key)

    assert err.value.code == "E_SYNC_BOOTSTRAP_NOT_ALLOWED"
    assert "install-if-missing recovery" in str(err.value)
    assert "ghdp sync run --capability example-capability" in str(err.value)
    assert "ghdp sync repair --capability example-capability" in str(err.value)


def test_repair_content_rejects_unresolvable_bootstrap_target(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _build_manifest(
        "1.0.0",
        target_root_key="repo_root",
        target_subdir=".ghdp/synced/repo_ready",
    )
    tag_to_manifest = {"v1.0.0": manifest}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    index_payload = _build_index("v1.0.0", "1.0.0", allow_install_if_missing=True)

    def _root_resolver(root_key: str) -> Path:
        raise PlatformError("missing repo root", code="E_SYNC_ROOT_KEY_REQUIRES_REPO", reason=root_key)

    monkeypatch.setattr(release_content, "run_cmd", _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload))

    preview = release_content.preview_content_updates(resolve_root_key=_root_resolver)
    item = preview["capabilities"][0]

    assert item["bootstrap_allowed"] is False
    assert item["recovery_mode"] == "blocked"
    assert item["local_status"] == "scope_required"

    with pytest.raises(PlatformError) as err:
        release_content.repair_content("example-capability", resolve_root_key=_root_resolver)

    assert err.value.code == "E_SYNC_ROOT_KEY_UNRESOLVABLE"
    assert "ghdp sync run --capability example-capability" in str(err.value)
    assert "ghdp sync repair --capability example-capability" in str(err.value)


def test_apply_content_update_reports_sync_retry_hint_when_latest_manifest_drops_tracked_files(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {
        "v1.0.0": _build_manifest("1.0.0"),
        "v2.0.0": _build_manifest(
            "2.0.0",
            files=[
                {"asset_name": "a.txt", "target_path": "a.txt"},
            ],
        ),
    }
    tag_to_assets = {
        "v1.0.0": {"a.txt": b"a", "b.txt": b"b"},
        "v2.0.0": {"a.txt": b"a2"},
    }
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )

    with pytest.raises(PlatformError) as err:
        release_content.apply_content_update("example-capability")

    assert err.value.code == "E_SYNC_UPDATE_BLOCKED"
    assert "ghdp sync run --capability example-capability" in str(err.value)
    assert "ghdp sync repair --capability example-capability" in str(err.value)


def test_repair_content_reports_sync_retry_hint_when_recorded_manifest_is_stale(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )
    target_root = isolated_home / ".test-root" / "bundle"
    (target_root / "nested" / "b.txt").unlink()

    stale_manifest = {
        "v1.0.0": _build_manifest(
            "1.0.0",
            files=[
                {"asset_name": "a.txt", "target_path": "a.txt"},
            ],
        )
    }
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(stale_manifest, tag_to_assets))

    with pytest.raises(PlatformError) as err:
        release_content.repair_content("example-capability")

    assert err.value.code == "E_SYNC_REPAIR_FAILED"
    assert "ghdp sync run --capability example-capability" in str(err.value)
    assert "ghdp sync repair --capability example-capability" in str(err.value)


def test_apply_content_update_updates_only_existing_tracked_files(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {
        "v1.0.0": _build_manifest("1.0.0"),
        "v2.0.0": _build_manifest(
            "2.0.0",
            files=[
                {"asset_name": "a.txt", "target_path": "a.txt"},
                {"asset_name": "b.txt", "target_path": "nested/b.txt"},
                {"asset_name": "c.txt", "target_path": "new/c.txt"},
            ],
        ),
    }
    tag_to_assets = {
        "v1.0.0": {"a.txt": b"a", "b.txt": b"b"},
        "v2.0.0": {"a.txt": b"a2", "b.txt": b"b2", "c.txt": b"c2"},
    }
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )

    result = release_content.apply_content_update("example-capability")
    target_root = isolated_home / ".test-root" / "bundle"

    assert result["updated_count"] == 2
    assert (target_root / "a.txt").read_bytes() == b"a2"
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b2"
    assert not (target_root / "new" / "c.txt").exists()


def test_repair_content_restores_missing_files_from_installed_version(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )
    target_root = isolated_home / ".test-root" / "bundle"
    (target_root / "nested" / "b.txt").unlink()

    result = release_content.repair_content("example-capability")

    assert result["repaired_count"] == 1
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b"


def test_scan_content_inventory_detects_extra_local_files_and_persists_them(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v2.0.0": _build_manifest("2.0.0")}
    tag_to_assets = {"v2.0.0": {"a.txt": b"a2", "b.txt": b"b2"}}
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))
    monkeypatch.setattr(release_content, "_default_resolve_root_key", _resolve_root_key)

    target_root = isolated_home / ".test-root" / "bundle"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "a.txt").write_bytes(b"a2")
    (target_root / "nested").mkdir(parents=True, exist_ok=True)
    (target_root / "nested" / "b.txt").write_bytes(b"b2")
    (target_root / "manual.txt").write_text("manual", encoding="utf-8")

    result = release_content.scan_content_inventory(persist=True)
    item = result["capabilities"][0]
    state = get_tool_state("content:example-capability")

    assert item["local_status"] == "detected_current"
    assert item["extra_local_files"] == ["manual.txt"]
    assert state["detected_local_files"] == ["manual.txt"]
    assert state["source"] == "detected"
    assert state["provider"] == "github_release"
    assert state["provider_source"]["tag"] == "v2.0.0"


def test_run_sync_actions_repairs_then_updates_same_capability(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {
        "v1.0.0": _build_manifest("1.0.0"),
        "v2.0.0": _build_manifest("2.0.0"),
    }
    tag_to_assets = {
        "v1.0.0": {"a.txt": b"a1", "b.txt": b"b1"},
        "v2.0.0": {"a.txt": b"a2", "b.txt": b"b2"},
    }
    monkeypatch.setattr(release_content, "run_cmd", _install_assets(tag_to_manifest, tag_to_assets))

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )
    target_root = isolated_home / ".test-root" / "bundle"
    (target_root / "nested" / "b.txt").unlink()

    result = release_content.run_sync_actions(apply=True)

    assert result["results"]["repairs"][0]["repaired_count"] == 1
    assert result["results"]["updates"][0]["updated_count"] >= 1
    assert (target_root / "a.txt").read_bytes() == b"a2"
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b2"


def test_run_sync_actions_bootstraps_missing_capability_when_allowed(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    index_payload = _build_index("v1.0.0", "1.0.0", allow_install_if_missing=True)
    monkeypatch.setattr(release_content, "run_cmd", _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload))

    result = release_content.run_sync_actions(apply=True, resolve_root_key=_resolve_root_key)
    target_root = isolated_home / ".test-root" / "bundle"

    assert result["installs"][0]["action"] == "install"
    assert result["results"]["installs"][0]["latest_version"] == "1.0.0"
    assert result["results"]["installs"][0]["source"] == "release"
    assert (target_root / "a.txt").read_bytes() == b"a"
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b"


def test_preview_content_updates_does_not_treat_untracked_shared_root_files_as_installed(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_files = [
        {"asset_name": "shared-a.txt", "target_path": "shared-a.txt"},
        {"asset_name": "shared-b.txt", "target_path": "shared-b.txt"},
    ]
    tag_to_manifest = {
        "shared-v1.0.0": _build_manifest(
            "1.0.0",
            files=shared_files,
            capability="shared-capability",
            target_subdir=".",
        )
    }
    tag_to_assets = {"shared-v1.0.0": {"shared-a.txt": b"a", "shared-b.txt": b"b"}}
    index_payload = _build_index(
        "shared-v1.0.0",
        "1.0.0",
        capability="shared-capability",
        target_subdir=".",
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload),
    )

    target_root = isolated_home / ".test-root"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "manual.txt").write_text("manual", encoding="utf-8")

    preview = release_content.preview_content_updates(resolve_root_key=_resolve_root_key)
    item = preview["capabilities"][0]

    assert item["installed"] is False
    assert item["local_status"] == "not_installed"
    assert item["action"] == "blocked"
    assert item["recovery_detail"] == "install_if_missing_disabled"
    assert item["extra_local_files"] == ["manual.txt"]
    assert item["missing_local_files"] == ["shared-a.txt", "shared-b.txt"]


def test_preview_content_updates_keeps_recorded_shared_root_installs_repairable(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_files = [
        {"asset_name": "shared-a.txt", "target_path": "shared-a.txt"},
        {"asset_name": "shared-b.txt", "target_path": "shared-b.txt"},
    ]
    tag_to_manifest = {
        "shared-v1.0.0": _build_manifest(
            "1.0.0",
            files=shared_files,
            capability="shared-capability",
            target_subdir=".",
        )
    }
    tag_to_assets = {"shared-v1.0.0": {"shared-a.txt": b"a", "shared-b.txt": b"b"}}
    index_payload = _build_index(
        "shared-v1.0.0",
        "1.0.0",
        capability="shared-capability",
        target_subdir=".",
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload),
    )

    release_content.install_release_content(
        capability="shared-capability",
        repo="owner/repo",
        tag="shared-v1.0.0",
        resolve_root_key=_resolve_root_key,
    )
    target_root = isolated_home / ".test-root"
    (target_root / "shared-a.txt").unlink()
    (target_root / "shared-b.txt").unlink()
    (target_root / "manual.txt").write_text("manual", encoding="utf-8")

    preview = release_content.preview_content_updates(resolve_root_key=_resolve_root_key)
    item = preview["capabilities"][0]

    assert item["installed"] is True
    assert item["local_status"] == "partial"
    assert item["action"] == "repair"
    assert item["extra_local_files"] == ["manual.txt"]
    assert item["missing_local_files"] == ["shared-a.txt", "shared-b.txt"]


def test_preview_content_updates_does_not_treat_stale_detected_shared_root_state_as_repairable(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_files = [
        {"asset_name": "shared-a.txt", "target_path": "shared-a.txt"},
        {"asset_name": "shared-b.txt", "target_path": "shared-b.txt"},
    ]
    tag_to_manifest = {
        "shared-v1.0.0": _build_manifest(
            "1.0.0",
            files=shared_files,
            capability="shared-capability",
            target_subdir=".",
        )
    }
    tag_to_assets = {"shared-v1.0.0": {"shared-a.txt": b"a", "shared-b.txt": b"b"}}
    index_payload = _build_index(
        "shared-v1.0.0",
        "1.0.0",
        capability="shared-capability",
        target_subdir=".",
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload),
    )

    target_root = isolated_home / ".test-root"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "manual.txt").write_text("manual", encoding="utf-8")
    update_tool_state(
        "content:shared-capability",
        {
            "capability": "shared-capability",
            "provider": "github_release",
            "provider_source": {
                "repo": "owner/repo",
                "tag": "shared-v1.0.0",
                "manifest_asset": "content-manifest.json",
            },
            "package_type": "file_bundle",
            "target_type": "filesystem",
            "policy": {},
            "repo": "owner/repo",
            "tag": "shared-v1.0.0",
            "version": "1.0.0",
            "manifest_asset": "content-manifest.json",
            "install_path": str(target_root),
            "files": ["shared-a.txt", "shared-b.txt"],
            "detected_local_files": ["manual.txt"],
            "source": "detected",
        },
    )

    preview = release_content.preview_content_updates(resolve_root_key=_resolve_root_key)
    item = preview["capabilities"][0]

    assert item["installed"] is False
    assert item["local_status"] == "not_installed"
    assert item["action"] == "blocked"
    assert item["recovery_detail"] == "install_if_missing_disabled"
    assert item["extra_local_files"] == ["manual.txt"]
    assert item["missing_local_files"] == ["shared-a.txt", "shared-b.txt"]


def test_repair_content_rejects_stale_detected_shared_root_state_without_bootstrap_policy(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_files = [
        {"asset_name": "shared-a.txt", "target_path": "shared-a.txt"},
        {"asset_name": "shared-b.txt", "target_path": "shared-b.txt"},
    ]
    tag_to_manifest = {
        "shared-v1.0.0": _build_manifest(
            "1.0.0",
            files=shared_files,
            capability="shared-capability",
            target_subdir=".",
        )
    }
    tag_to_assets = {"shared-v1.0.0": {"shared-a.txt": b"a", "shared-b.txt": b"b"}}
    index_payload = _build_index(
        "shared-v1.0.0",
        "1.0.0",
        capability="shared-capability",
        target_subdir=".",
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload),
    )

    target_root = isolated_home / ".test-root"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "manual.txt").write_text("manual", encoding="utf-8")
    update_tool_state(
        "content:shared-capability",
        {
            "capability": "shared-capability",
            "provider": "github_release",
            "provider_source": {
                "repo": "owner/repo",
                "tag": "shared-v1.0.0",
                "manifest_asset": "content-manifest.json",
            },
            "package_type": "file_bundle",
            "target_type": "filesystem",
            "policy": {},
            "repo": "owner/repo",
            "tag": "shared-v1.0.0",
            "version": "1.0.0",
            "manifest_asset": "content-manifest.json",
            "install_path": str(target_root),
            "files": ["shared-a.txt", "shared-b.txt"],
            "detected_local_files": ["manual.txt"],
            "source": "detected",
        },
    )

    with pytest.raises(PlatformError) as err:
        release_content.repair_content("shared-capability", resolve_root_key=_resolve_root_key)

    assert err.value.code == "E_SYNC_BOOTSTRAP_NOT_ALLOWED"
    assert "install-if-missing recovery" in str(err.value)
    assert not (target_root / "shared-a.txt").exists()
    assert not (target_root / "shared-b.txt").exists()


def test_run_sync_actions_leaves_blocked_shared_root_capability_uninstalled_while_updating_others(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_files = [
        {"asset_name": "shared-a.txt", "target_path": "shared-a.txt"},
        {"asset_name": "shared-b.txt", "target_path": "shared-b.txt"},
    ]
    tag_to_manifest = {
        "v1.0.0": _build_manifest("1.0.0"),
        "v2.0.0": _build_manifest("2.0.0"),
        "shared-v1.0.0": _build_manifest(
            "1.0.0",
            files=shared_files,
            capability="shared-capability",
            target_subdir=".",
        ),
    }
    tag_to_assets = {
        "v1.0.0": {"a.txt": b"a1", "b.txt": b"b1"},
        "v2.0.0": {"a.txt": b"a2", "b.txt": b"b2"},
        "shared-v1.0.0": {"shared-a.txt": b"sa1", "shared-b.txt": b"sb1"},
    }

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        asset_name = cmd[cmd.index("--pattern") + 1]
        download_dir = Path(cmd[cmd.index("--dir") + 1])
        tag = cmd[3]
        if asset_name == "content-index.json":
            payload = {
                "schema_version": "1.0",
                "generated_at": "2026-03-18T13:25:00Z",
                "capabilities": [
                    {
                        "capability": "example-capability",
                        "version": "2.0.0",
                        "provider": "github_release",
                        "source": {
                            "repo": "owner/repo",
                            "tag": "v2.0.0",
                            "manifest_asset": "content-manifest.json",
                        },
                        "package_type": "file_bundle",
                        "target_type": "filesystem",
                        "policy": {
                            "allow_update_existing_files": True,
                            "allow_new_files_on_update": False,
                            "min_cli_version": "0.1.0",
                        },
                    },
                    {
                        "capability": "shared-capability",
                        "version": "1.0.0",
                        "provider": "github_release",
                        "source": {
                            "repo": "owner/repo",
                            "tag": "shared-v1.0.0",
                            "manifest_asset": "content-manifest.json",
                        },
                        "package_type": "file_bundle",
                        "target_type": "filesystem",
                        "policy": {
                            "allow_update_existing_files": True,
                            "allow_new_files_on_update": False,
                            "min_cli_version": "0.1.0",
                        },
                    },
                ],
            }
            (download_dir / asset_name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return
        if asset_name == "content-manifest.json":
            (download_dir / asset_name).write_text(json.dumps(tag_to_manifest[tag], indent=2), encoding="utf-8")
            return
        asset_map = tag_to_assets[tag]
        if asset_name not in asset_map:
            raise PlatformError("missing asset", code="E_CMD_FAILED", reason=asset_name)
        (download_dir / asset_name).write_bytes(asset_map[asset_name])

    monkeypatch.setattr(release_content, "run_cmd", _fake_run)

    release_content.install_release_content(
        capability="example-capability",
        repo="owner/repo",
        tag="v1.0.0",
        resolve_root_key=_resolve_root_key,
    )
    target_root = isolated_home / ".test-root"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "manual.txt").write_text("manual", encoding="utf-8")

    result = release_content.run_sync_actions(apply=True, resolve_root_key=_resolve_root_key)

    assert any(item["capability"] == "shared-capability" for item in result["blocked"])
    assert result["results"]["repairs"] == []
    assert result["results"]["updates"][0]["capability"] == "example-capability"
    assert (target_root / "bundle" / "a.txt").read_bytes() == b"a2"
    assert (target_root / "bundle" / "nested" / "b.txt").read_bytes() == b"b2"
    assert not (target_root / "shared-a.txt").exists()
    assert not (target_root / "shared-b.txt").exists()


def test_preview_content_updates_carries_recovery_hint_from_capability_metadata(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag_to_manifest = {"v1.0.0": _build_manifest("1.0.0")}
    tag_to_assets = {"v1.0.0": {"a.txt": b"a", "b.txt": b"b"}}
    index_payload = _build_index("v1.0.0", "1.0.0")
    capability = index_payload["capabilities"][0]
    assert isinstance(capability, dict)
    capability["recovery_hint"] = "Run 'ghdp tableau init' to bootstrap Tableau Athena drivers."
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _install_assets_with_index(tag_to_manifest, tag_to_assets, index_payload),
    )

    preview = release_content.preview_content_updates(resolve_root_key=_resolve_root_key)
    item = preview["capabilities"][0]

    assert item["recovery_hint"] == "Run 'ghdp tableau init' to bootstrap Tableau Athena drivers."
    assert item["action"] == "blocked"


def test_install_content_entry_supports_registered_provider_and_target_handler(
    isolated_home: Path,
) -> None:
    provider_name = _register_mock_provider(
        manifest=_build_manifest("3.0.0"),
        assets={"a.txt": b"a3", "b.txt": b"b3"},
        provider_name="mock_provider_install",
    )

    class _MockTargetHandler:
        name = "mock_target"

        def resolve_install_root(self, *, root_key: str, target_subdir: str, resolve_root_key):  # type: ignore[no-untyped-def]
            return resolve_root_key(root_key) / "provider-ready" / target_subdir

    register_target_handler(_MockTargetHandler())

    result = release_content.install_content_entry(
        {
            "capability": "example-capability",
            "provider": provider_name,
            "source": {"catalog": "mock"},
            "package_type": "file_bundle",
            "target_type": "mock_target",
            "policy": {"allow_update_existing_files": True},
        },
        resolve_root_key=_resolve_root_key,
    )

    target_root = isolated_home / ".test-root" / "provider-ready" / "bundle"
    state = get_tool_state("content:example-capability")

    assert result["source"] == "release"
    assert result["content_version"] == "3.0.0"
    assert (target_root / "a.txt").read_bytes() == b"a3"
    assert (target_root / "nested" / "b.txt").read_bytes() == b"b3"
    assert state["provider"] == provider_name
    assert state["target_type"] == "mock_target"


def test_scan_content_inventory_supports_provider_backed_registry_entries(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_name = _register_mock_provider(
        manifest=_build_manifest("4.0.0"),
        assets={"a.txt": b"a4", "b.txt": b"b4"},
        provider_name="mock_provider_scan",
    )

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        asset_name = cmd[cmd.index("--pattern") + 1]
        download_dir = Path(cmd[cmd.index("--dir") + 1])
        if asset_name != "content-index.json":
            raise PlatformError("unexpected asset", code="E_CMD_FAILED", reason=asset_name)
        payload = {
            "schema_version": "1.0",
            "generated_at": "2026-03-20T14:00:00Z",
            "capabilities": [
                {
                    "capability": "example-capability",
                    "version": "4.0.0",
                    "provider": provider_name,
                    "source": {"catalog": "mock"},
                    "package_type": "file_bundle",
                    "target_type": "filesystem",
                    "policy": {
                        "allow_update_existing_files": True,
                        "allow_new_files_on_update": False,
                    },
                }
            ],
        }
        (download_dir / asset_name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    monkeypatch.setattr(release_content, "run_cmd", _fake_run)
    monkeypatch.setattr(release_content, "_default_resolve_root_key", _resolve_root_key)

    target_root = isolated_home / ".test-root" / "bundle"
    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "a.txt").write_bytes(b"a4")
    (target_root / "nested").mkdir(parents=True, exist_ok=True)
    (target_root / "nested" / "b.txt").write_bytes(b"b4")

    result = release_content.scan_content_inventory(persist=True)
    item = result["capabilities"][0]
    state = get_tool_state("content:example-capability")

    assert item["local_status"] == "detected_current"
    assert item["provider"] == provider_name
    assert state["provider"] == provider_name
    assert state["provider_source"] == {"catalog": "mock", "repo": "", "tag": "", "manifest_asset": "content-manifest.json"}


def test_fetch_content_index_appends_allowlisted_marketplace_entries(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_skill(repo_root, "skills/skill-workbench", {"SKILL.md": "# skill-workbench\n"})
    _write_marketplace_plugin(
        repo_root,
        "query-athena",
        {"query-athena": {"SKILL.md": "# query-athena\n", "references/runbook.md": "athena"}},
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="cafebabe12345678",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="claude_skills",
                            target_root_key="claude_skills_root",
                            target_subdir="skill-workbench",
                            category="claude_skills",
                        ),
                        _marketplace_entry(
                            capability="marketplace-claude-plugin-query-athena",
                            install_unit_type="plugin",
                            source_path="plugins/query-athena",
                            target_type="claude_plugins",
                            target_root_key="claude_plugins_root",
                            target_subdir="query-athena",
                            category="claude_plugins",
                        ),
                    ],
                    "codex": [
                        _marketplace_entry(
                            capability="marketplace-codex-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="codex_skills",
                            target_root_key="codex_skills_root",
                            target_subdir="skill-workbench",
                            category="codex_skills",
                        ),
                        _marketplace_entry(
                            capability="marketplace-codex-plugin-query-athena",
                            install_unit_type="plugin",
                            source_path="plugins/query-athena",
                            target_type="codex_plugins",
                            target_root_key="codex_plugins_root",
                            target_subdir="query-athena",
                            category="codex_plugins",
                        ),
                    ],
                },
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    result = release_content.fetch_content_index()
    capabilities = {str(item["capability"]): item for item in result["capabilities"]}

    assert "marketplace-skill-allowlist" in capabilities
    assert "marketplace-claude-skill-skill-workbench" in capabilities
    assert "marketplace-claude-plugin-query-athena" in capabilities
    assert "marketplace-codex-skill-skill-workbench" in capabilities
    assert "marketplace-codex-plugin-query-athena" in capabilities
    assert capabilities["marketplace-claude-plugin-query-athena"]["target_type"] == "claude_plugins"
    assert capabilities["marketplace-claude-plugin-query-athena"]["category"] == "claude_plugins"
    assert capabilities["marketplace-codex-plugin-query-athena"]["target_type"] == "codex_plugins"
    assert capabilities["marketplace-codex-plugin-query-athena"]["category"] == "codex_plugins"
    assert capabilities["marketplace-claude-skill-skill-workbench"]["provider"] == "marketplace_repo"
    assert capabilities["marketplace-claude-skill-skill-workbench"]["source"]["commit"] == "cafebabe12345678"
    assert capabilities["marketplace-claude-skill-skill-workbench"]["allow_install_if_missing"] is True
    assert capabilities["marketplace-claude-skill-skill-workbench"]["target_type"] == "claude_skills"
    assert capabilities["marketplace-codex-plugin-query-athena"]["source"]["source_path"] == "plugins/query-athena"


def test_preview_content_updates_marks_allowlisted_marketplace_skill_for_install(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_skill(repo_root, "skills/skill-workbench", {"SKILL.md": "# skill-workbench\n"})
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="1234abcd5678ef90",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="claude_skills",
                            target_root_key="claude_skills_root",
                            target_subdir="skill-workbench",
                            category="claude_skills",
                        )
                    ]
                },
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    preview = release_content.preview_content_updates(capability="marketplace-claude-skill-skill-workbench")
    item = preview["capabilities"][0]

    assert item["installed"] is False
    assert item["action"] == "install"
    assert item["latest_version"] == "1234abcd5678ef90"
    assert item["install_path"].endswith(str(Path(".claude") / "skills" / "skill-workbench"))


def test_run_sync_actions_installs_allowlisted_claude_plugin_root(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_plugin(
        repo_root,
        "query-athena",
        {
            "query-athena": {
                "SKILL.md": "# query-athena\n",
                "references/runbook.md": "athena",
            }
        },
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="abcddcba11112222",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-plugin-query-athena",
                            install_unit_type="plugin",
                            source_path="plugins/query-athena",
                            target_type="claude_plugins",
                            target_root_key="claude_plugins_root",
                            target_subdir="query-athena",
                            category="claude_plugins",
                        )
                    ]
                },
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    result = release_content.run_sync_actions(apply=True)
    plugin_root = isolated_home / ".claude" / "plugins" / "query-athena"
    state = get_tool_state("content:marketplace-claude-plugin-query-athena")

    installed_caps = [item["capability"] for item in result["results"]["installs"]]
    assert "marketplace-claude-plugin-query-athena" in installed_caps
    assert (plugin_root / ".claude-plugin" / "plugin.json").exists()
    assert (plugin_root / "skills" / "query-athena" / "SKILL.md").read_text(encoding="utf-8") == "# query-athena\n"
    assert state["target_type"] == "claude_plugins"
    assert state["category"] == "claude_plugins"
    assert state["install_path"] == str(plugin_root)


def test_run_sync_actions_installs_explicit_codex_plugin_root(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_plugin(
        repo_root,
        "query-athena",
        {
            "query-athena": {
                "SKILL.md": "# query-athena\n",
                "references/runbook.md": "athena",
            }
        },
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="2222333344445555",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "codex": [
                        _marketplace_entry(
                            capability="marketplace-codex-plugin-query-athena",
                            install_unit_type="plugin",
                            source_path="plugins/query-athena",
                            target_type="codex_plugins",
                            target_root_key="codex_plugins_root",
                            target_subdir="query-athena",
                            category="codex_plugins",
                        )
                    ]
                },
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    result = release_content.run_sync_actions(apply=True)
    target_root = isolated_home / ".codex" / "plugins" / "query-athena"
    state = get_tool_state("content:marketplace-codex-plugin-query-athena")

    installed_caps = [item["capability"] for item in result["results"]["installs"]]
    assert "marketplace-codex-plugin-query-athena" in installed_caps
    assert (target_root / ".codex-plugin" / "plugin.json").exists()
    assert (target_root / "skills" / "query-athena" / "SKILL.md").read_text(encoding="utf-8") == "# query-athena\n"
    assert (target_root / "skills" / "query-athena" / "references" / "runbook.md").read_text(encoding="utf-8") == "athena"
    assert state["target_type"] == "codex_plugins"
    assert state["category"] == "codex_plugins"
    assert state["install_path"] == str(target_root)


def test_fetch_content_index_prefers_repo_policy_when_present(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    (repo_root / ".git").mkdir(parents=True, exist_ok=True)
    _write_marketplace_skill(repo_root, "skills/skill-workbench", {"SKILL.md": "# skill-workbench\n"})
    _write_marketplace_skill(repo_root, "skills/git-branch-review", {"SKILL.md": "# git-branch-review\n"})
    _write_repo_marketplace_policy_v2(
        repo_root,
        target_entries={
            "claude": [
                _marketplace_entry(
                    capability="marketplace-claude-skill-git-branch-review",
                    install_unit_type="skill",
                    source_path="skills/git-branch-review",
                    target_type="claude_skills",
                    target_root_key="claude_skills_root",
                    target_subdir="git-branch-review",
                    category="claude_skills",
                )
            ]
        },
    )
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="repooverride11112222",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="claude_skills",
                            target_root_key="claude_skills_root",
                            target_subdir="skill-workbench",
                            category="claude_skills",
                        )
                    ]
                },
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    result = release_content.fetch_content_index()
    capabilities = {str(item["capability"]): item for item in result["capabilities"]}

    assert "marketplace-claude-skill-git-branch-review" in capabilities
    assert "marketplace-claude-skill-skill-workbench" not in capabilities


def test_fetch_content_index_supports_legacy_codex_plugin_lists(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_plugin(
        repo_root,
        "query-athena",
        {"query-athena": {"SKILL.md": "# query-athena\n"}},
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="legacycodexplugin1111",
            allowlist_payload=_managed_allowlist_payload(
                repo_path=repo_root,
                targets={"codex": {"plugins": ["query-athena"]}},
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    result = release_content.fetch_content_index()
    capabilities = {str(item["capability"]): item for item in result["capabilities"]}

    assert "marketplace-codex-plugin-query-athena" in capabilities
    assert capabilities["marketplace-codex-plugin-query-athena"]["target_type"] == "codex_plugins"
    assert capabilities["marketplace-codex-plugin-query-athena"]["category"] == "codex_plugins"
    assert capabilities["marketplace-codex-plugin-query-athena"]["source"]["source_path"] == "plugins/query-athena"


def test_summarize_sync_categories_groups_marketplace_actions() -> None:
    summary = release_content.summarize_sync_categories(
        [
            {"capability": "marketplace-skill-allowlist", "category": "ghdp_policy", "action": "install"},
            {"capability": "marketplace-claude-plugin-query-athena", "category": "claude_plugins", "action": "install"},
            {"capability": "marketplace-claude-skill-skill-workbench", "category": "claude_skills", "action": "update"},
            {"capability": "marketplace-codex-plugin-query-athena", "category": "codex_plugins", "action": "install"},
            {"capability": "marketplace-codex-skill-skill-workbench", "category": "codex_skills", "action": "none"},
        ]
    )

    by_category = {str(item["category"]): item for item in summary}
    assert by_category["ghdp_policy"]["installs"] == 1
    assert by_category["claude_plugins"]["installs"] == 1
    assert by_category["claude_skills"]["updates"] == 1
    assert by_category["codex_plugins"]["installs"] == 1
    assert by_category["codex_skills"]["none"] == 1


def test_fetch_content_index_filters_capabilities_for_selected_team(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_skill(repo_root, "skills/skill-workbench", {"SKILL.md": "# skill-workbench\n"})
    _write_marketplace_plugin(repo_root, "query-athena", {"query-athena": {"SKILL.md": "# query-athena\n"}})
    monkeypatch.setenv("USERPROFILE", str(isolated_home))
    monkeypatch.setenv("HOME", str(isolated_home))
    (isolated_home / ".ghdp").mkdir(parents=True, exist_ok=True)
    (isolated_home / ".ghdp" / "config.json").write_text(json.dumps({"team.selected": "inform"}, indent=2), encoding="utf-8")
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="teamfilter11112222",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="claude_skills",
                            target_root_key="claude_skills_root",
                            target_subdir="skill-workbench",
                            category="claude_skills",
                        ),
                        _marketplace_entry(
                            capability="marketplace-claude-plugin-query-athena",
                            install_unit_type="plugin",
                            source_path="plugins/query-athena",
                            target_type="claude_plugins",
                            target_root_key="claude_plugins_root",
                            target_subdir="query-athena",
                            category="claude_plugins",
                        ),
                    ]
                },
            ),
            team_policy_payload=_managed_team_policy_payload(
                teams={
                    "default": {"allow_categories": ["claude_skills"]},
                    "inform": {"allow_categories": ["claude_skills"]},
                }
            ),
            base_capabilities=[_managed_allowlist_index_entry(), _managed_team_policy_index_entry()],
        ),
    )

    result = release_content.fetch_content_index()
    capabilities = {str(item["capability"]) for item in result["capabilities"]}

    assert result["active_team"] == "inform"
    assert result["active_team_source"] == "config"
    assert "ghdp-team-policy" in capabilities
    assert "marketplace-skill-allowlist" in capabilities
    assert "marketplace-claude-skill-skill-workbench" in capabilities
    assert "marketplace-claude-plugin-query-athena" not in capabilities


def test_fetch_content_index_falls_back_to_default_team_policy_when_team_not_selected(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_skill(repo_root, "skills/skill-workbench", {"SKILL.md": "# skill-workbench\n"})
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="teamdefault33334444",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="claude_skills",
                            target_root_key="claude_skills_root",
                            target_subdir="skill-workbench",
                            category="claude_skills",
                        )
                    ]
                },
            ),
            team_policy_payload=_managed_team_policy_payload(
                teams={
                    "default": {"deny_categories": ["claude_skills"]},
                }
            ),
            base_capabilities=[_managed_allowlist_index_entry(), _managed_team_policy_index_entry()],
        ),
    )

    result = release_content.fetch_content_index()
    capabilities = {str(item["capability"]) for item in result["capabilities"]}

    assert result["active_team"] == "default"
    assert result["active_team_source"] == "default"
    assert "ghdp-team-policy" in capabilities
    assert "marketplace-skill-allowlist" in capabilities
    assert "marketplace-claude-skill-skill-workbench" not in capabilities


def test_preview_content_updates_treats_commit_drift_without_byte_change_as_no_action(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_skill(repo_root, "skills/skill-workbench", {"SKILL.md": "# skill-workbench\n"})
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="1111222233334444",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="claude_skills",
                            target_root_key="claude_skills_root",
                            target_subdir="skill-workbench",
                            category="claude_skills",
                        )
                    ]
                },
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )
    release_content.run_sync_actions(apply=True)

    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="5555666677778888",
            allowlist_payload=_managed_allowlist_payload_v2(
                repo_path=repo_root,
                target_entries={
                    "claude": [
                        _marketplace_entry(
                            capability="marketplace-claude-skill-skill-workbench",
                            install_unit_type="skill",
                            source_path="skills/skill-workbench",
                            target_type="claude_skills",
                            target_root_key="claude_skills_root",
                            target_subdir="skill-workbench",
                            category="claude_skills",
                        )
                    ]
                },
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    preview = release_content.preview_content_updates(capability="marketplace-claude-skill-skill-workbench")
    item = preview["capabilities"][0]

    assert item["latest_version"] == "5555666677778888"
    assert item["updatable_files"] == []
    assert item["action"] == "none"


def test_preview_content_updates_marks_managed_team_policy_for_install(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="teampolicypreview1111",
            team_policy_payload=_managed_team_policy_payload(
                teams={"default": {"allow_categories": ["claude_skills", "codex_skills"]}}
            ),
            base_capabilities=[_managed_team_policy_index_entry()],
        ),
    )

    preview = release_content.preview_content_updates(capability="ghdp-team-policy")
    item = preview["capabilities"][0]

    assert item["installed"] is False
    assert item["action"] == "install"
    assert item["category"] == "ghdp_policy"
    assert item["install_path"].endswith(str(Path(".ghdp") / "policies"))


def test_run_sync_actions_installs_allowlisted_marketplace_skill(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_skill(
        repo_root,
        "skills/skill-workbench",
        {
            "SKILL.md": "# skill-workbench\n",
            "references/guide.md": "guide",
        },
    )
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="deadbeef00001111",
            allowlist_payload=_managed_allowlist_payload(
                repo_path=repo_root,
                targets={"claude": {"skills": ["skill-workbench"]}},
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    result = release_content.run_sync_actions(apply=True)
    target_root = isolated_home / ".claude" / "skills" / "skill-workbench"
    policy_path = isolated_home / ".ghdp" / "policies" / "capability-allowlist.managed.json"
    state = get_tool_state("content:marketplace-claude-skill-skill-workbench")

    installed_caps = [item["capability"] for item in result["results"]["installs"]]
    assert "marketplace-skill-allowlist" in installed_caps
    assert "marketplace-claude-skill-skill-workbench" in installed_caps
    assert policy_path.exists()
    assert (target_root / "SKILL.md").read_text(encoding="utf-8") == "# skill-workbench\n"
    assert (target_root / "references" / "guide.md").read_text(encoding="utf-8") == "guide"
    assert state["provider"] == "marketplace_repo"
    assert state["provider_source"]["commit"] == "deadbeef00001111"


def test_repair_content_blocks_marketplace_skill_removed_from_allowlist(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = isolated_home / "marketplace-repo"
    _write_marketplace_skill(repo_root, "skills/skill-workbench", {"SKILL.md": "# skill-workbench\n"})
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="feedface22223333",
            allowlist_payload=_managed_allowlist_payload(
                repo_path=repo_root,
                targets={"claude": {"skills": ["skill-workbench"]}},
            ),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    release_content.run_sync_actions(apply=True)
    target_root = isolated_home / ".claude" / "skills" / "skill-workbench"
    (target_root / "SKILL.md").unlink()
    monkeypatch.setattr(
        release_content,
        "run_cmd",
        _marketplace_run(
            repo_root,
            commit="feedface22223333",
            allowlist_payload=_managed_allowlist_payload(repo_path=repo_root, targets={}),
            base_capabilities=[_managed_allowlist_index_entry()],
        ),
    )

    with pytest.raises(PlatformError) as err:
        release_content.repair_content("marketplace-claude-skill-skill-workbench")

    assert err.value.code == "E_SYNC_POLICY_BLOCKED"


def test_team_toolset_source_bundle_builds_flat_release_assets(tmp_path: Path) -> None:
    build_script = _load_script_module("build_team_toolset_release_assets.py")
    source_root = Path(__file__).resolve().parents[1] / "release-assets" / "team_toolset"
    source_toolset = json.loads((source_root / "toolset.json").read_text(encoding="utf-8"))
    output_dir = tmp_path / "team-toolset-release"

    built_dir = build_script.build_assets(output_dir)
    manifest = json.loads((built_dir / "content-manifest.json").read_text(encoding="utf-8"))
    built_toolset = json.loads((built_dir / "toolset.json").read_text(encoding="utf-8"))

    assert built_dir == output_dir
    assert built_toolset == source_toolset
    assert manifest["capability"] == "ghdp-team-toolset"
    assert manifest["files"] == [{"asset_name": "toolset.json", "target_path": "team-toolset.managed.json"}]
    assert manifest["target_subdir"] == "policies"


def test_claude_athena_workgroup_bundle_builds_flat_release_assets(tmp_path: Path) -> None:
    build_script = _load_script_module("build_claude_athena_workgroup_release_assets.py")
    source_path = Path(__file__).resolve().parents[1] / "src" / "platform_cli" / "resources" / "claude" / "athena-workgroup-map.json"
    source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    output_dir = tmp_path / "claude-athena-workgroup-release"

    built_dir = build_script.build_assets(output_dir)
    manifest = json.loads((built_dir / "content-manifest.json").read_text(encoding="utf-8"))
    built_payload = json.loads((built_dir / "athena-workgroup-map.json").read_text(encoding="utf-8"))

    assert built_dir == output_dir
    assert built_payload == source_payload
    assert manifest["capability"] == "claude-athena-workgroup-map"
    assert manifest["files"] == [{"asset_name": "athena-workgroup-map.json", "target_path": "claude-athena-workgroup-map.managed.json"}]
    assert manifest["target_subdir"] == "policies"


def test_content_index_source_includes_team_toolset_and_builds_release_asset(tmp_path: Path) -> None:
    build_script = _load_script_module("build_content_index_release_asset.py")
    source_root = Path(__file__).resolve().parents[1] / "release-assets" / "content_index"
    source_index = json.loads((source_root / "content-index.json").read_text(encoding="utf-8"))
    output_dir = tmp_path / "content-index-release"

    built_dir = build_script.build_assets(output_dir)
    built_index = json.loads((built_dir / "content-index.json").read_text(encoding="utf-8"))

    capability_names = [str(item["capability"]) for item in source_index["capabilities"]]

    assert built_dir == output_dir
    assert built_index == source_index
    assert "repo-ready-assets" in capability_names
    assert "ghdp-team-toolset" in capability_names
    assert "claude-athena-workgroup-map" in capability_names
