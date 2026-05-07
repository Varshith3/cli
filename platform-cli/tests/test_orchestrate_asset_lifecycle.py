from __future__ import annotations

import json
from pathlib import Path

from platform_cli.tools.orchestrate_asset_lifecycle import run_asset_lifecycle
from platform_cli.tools.orchestrate_runtime import start_orchestrate_run
from orchestrate_stage_seed import seed_stage_contracts


def _seed_asset_repo(repo_root: Path) -> None:
    (repo_root / ".git").mkdir()
    (repo_root / ".ghdp" / "agents").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "skills").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "plugins").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "memory").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "orchestrate").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "frbr").mkdir(parents=True, exist_ok=True)

    (repo_root / ".ghdp" / "agents" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "agents": [
                    {
                        "id": "orchestrator",
                        "role": "control_plane",
                        "summary": "Owns orchestration runtime.",
                        "contract_path": ".ghdp/agents/orchestrator.json",
                    },
                    {
                        "id": "asset-lifecycle",
                        "role": "asset_operator",
                        "summary": "Owns lightweight asset lifecycle work.",
                        "contract_path": ".ghdp/agents/asset-lifecycle.json",
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "agents" / "orchestrator.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "orchestrator",
                "role": "control_plane",
                "stages_owned": ["stage0_trigger"],
                "allowed_skills": ["traceability-and-resume"],
                "allowed_plugins": ["native-memory-filesystem"],
                "produces_artifacts": ["branch_state.json"],
                "approval_mode": "policy_driven",
                "can_block": True,
                "can_retry": True,
                "prompt_contract": ["Own runtime state and keep branch artifacts in sync."],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "agents" / "asset-lifecycle.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "asset-lifecycle",
                "role": "asset_operator",
                "stages_owned": ["independent_asset_lifecycle"],
                "allowed_skills": ["asset-capability-discovery", "asset-lifecycle-operations", "traceability-and-resume"],
                "allowed_plugins": ["asset-lifecycle-sync", "native-memory-filesystem"],
                "produces_artifacts": ["asset_operation_result"],
                "approval_mode": "on_destructive_or_ambiguous_change",
                "can_block": True,
                "can_retry": True,
                "prompt_contract": ["Prefer lightweight asset-only handling when full SDLC is not needed."],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "skills" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "skills": [
                    {"id": "traceability-and-resume", "purpose": "Persist orchestration state."},
                    {"id": "asset-capability-discovery", "purpose": "Inspect capability assets."},
                    {"id": "asset-lifecycle-operations", "purpose": "Revise capability assets."},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "plugins" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "plugins": [
                    {"id": "native-memory-filesystem", "summary": "Shared memory."},
                    {"id": "asset-lifecycle-sync", "summary": "Asset lifecycle sync adapter."},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "plugins" / "asset-lifecycle-sync").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "plugins" / "asset-lifecycle-sync" / "plugin.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "asset-lifecycle-sync",
                "supported_operations": ["inventory", "create", "revise", "update_versioned_asset", "remove"],
                "known_asset_targets": [
                    {
                        "id": "toolset_codex_version",
                        "provider_family": "github_release",
                        "managed_by": "ghdp-team-toolset",
                        "files": [
                            "platform-cli/src/platform_cli/resources/manifests/toolset.json",
                            "platform-cli/release-assets/team_toolset/toolset.json"
                        ]
                    }
                ],
                "source_files": {
                    "github_release": ["platform-cli/release-assets/content_index/content-index.json"],
                    "marketplace_repo": [".ghdp/capability-allowlist.json"]
                },
                "setup_contract": ["Inventory before mutation."],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "memory" / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "partitions": [{"id": "shared"}]}, indent=2) + "\n",
        encoding="utf-8",
    )
    for rel_path in (
        ".ghdp/agents/AGENTS.md",
        ".ghdp/skills/SKILLS.md",
        ".ghdp/plugins/PLUGINS.md",
        ".ghdp/memory/README.md",
        ".ghdp/orchestrate/README.md",
        ".ghdp/memory/shared/README.md",
        ".ghdp/memory/context/README.md",
    ):
        (repo_root / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (repo_root / rel_path).write_text("# stub\n", encoding="utf-8")
    seed_stage_contracts(repo_root)
    (repo_root / ".ghdp" / "frbr" / "intent.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "ticket_key": "EPPE-7391",
                "branch_name": "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory",
                "summary": "Revise the team toolset asset to raise the Codex minimum version requirement only.",
                "intent": "Only update the existing team toolset asset and do not expand into broader SDLC.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    toolset_payload = {
        "schema_version": "0.0.1",
        "teams": {
            "data_engineer": {"tools": {"codex": {"op": ">=", "version": "0.120.0"}}},
            "data_scientist": {"tools": {"codex": {"op": ">=", "version": "0.120.0"}}},
        },
    }
    for rel_path in (
        "platform-cli/src/platform_cli/resources/manifests/toolset.json",
        "platform-cli/release-assets/team_toolset/toolset.json",
    ):
        path = repo_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(toolset_payload, indent=2) + "\n", encoding="utf-8")
    (repo_root / "platform-cli" / "release-assets" / "content_index").mkdir(parents=True, exist_ok=True)
    (repo_root / "platform-cli" / "release-assets" / "content_index" / "content-index.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "capabilities": [
                    {
                        "capability": "ghdp-team-toolset",
                        "provider": "github_release",
                        "repo": "gh-org-data-platform/dp-tools-local-setup",
                        "tag": "v0.128.0",
                        "manifest_asset": "team_toolset/content-manifest.json",
                        "target_type": "repo_file",
                        "category": "tooling",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "capability-allowlist.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "sources": {
                    "skill_marketplace": {
                        "repo": "gh-org-data-platform/gh-dp-data-platform-skill-marketplace",
                        "branch": "develop",
                        "targets": {
                            "codex": {
                                "entries": [
                                    {
                                        "capability": "marketplace-codex-skill-git-branch-review",
                                        "install_unit_type": "directory",
                                        "source_path": "codex/skills/git-branch-review",
                                        "target_type": "skill",
                                        "target_root_key": "codex_home",
                                        "target_subdir": "skills",
                                        "category": "skill",
                                    }
                                ]
                            }
                        },
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_asset_lifecycle_revises_toolset_codex_version(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_asset_repo(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.runtime_support.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_asset_lifecycle._capability_inventory",
        lambda _repo_root: [{"capability": "ghdp-team-toolset", "provider": "github_release"}],
    )

    start = start_orchestrate_run(repo_root=repo_root)
    result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="update_versioned_asset",
        asset_target="toolset_codex_version",
        new_version="0.128.0",
    )

    assert result.operation == "update_versioned_asset"
    assert sorted(result.changed_teams) == ["data_engineer", "data_scientist"]
    assert sorted(result.changed_files) == [
        "platform-cli/release-assets/team_toolset/toolset.json",
        "platform-cli/src/platform_cli/resources/manifests/toolset.json",
    ]

    for rel_path in result.changed_files:
        payload = json.loads((repo_root / rel_path).read_text(encoding="utf-8"))
        assert payload["teams"]["data_engineer"]["tools"]["codex"]["version"] == "0.128.0"
        assert payload["teams"]["data_scientist"]["tools"]["codex"]["version"] == "0.128.0"

    branch_runtime_root = next((repo_root / ".ghdp" / "orchestrate" / "branches").iterdir())
    run_root = branch_runtime_root / "runs" / start.active_run_key
    assert (run_root / "asset_operation_result.json").exists()


def test_asset_lifecycle_creates_and_revises_release_backed_capability(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_asset_repo(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.runtime_support.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_asset_lifecycle._capability_inventory", lambda _repo_root: [])
    start_orchestrate_run(repo_root=repo_root)

    payload_path = repo_root / "release_payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "capability": "ghdp-agent-policy",
                "version": "0.128.0",
                "provider": "github_release",
                "repo": "gh-org-data-platform/dp-tools-local-setup",
                "tag": "ghdp-agent-policy-v0.128.0",
                "target_type": "filesystem",
                "category": "repo_ready",
                "release_asset_dir": "ghdp_agent_policy",
                "target_root_key": "ghdp_user_root",
                "target_subdir": "policies",
                "files": [
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/policy/agent-policy.json",
                        "asset_name": "agent-policy.json",
                        "target_path": "agent-policy.managed.json",
                        "inline_json": {"schema_version": "1.0", "managed_by": "ghdp", "allow": ["sync.read"]},
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    create_result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="create",
        payload_file=payload_path,
        provider_family="github_release",
    )
    assert create_result.capability_id == "ghdp-agent-policy"
    assert create_result.provider_family == "github_release"
    assert create_result.bundle_contract_path == "platform-cli/release-assets/catalog/ghdp_agent_policy.json"
    assert create_result.built_bundle_dir
    built_bundle = Path(create_result.built_bundle_dir)
    assert (built_bundle / "content-manifest.json").exists()
    assert (built_bundle / "agent-policy.json").exists()

    payload_path.write_text(
        json.dumps(
            {
                "category": "repo_governance",
                "files": [
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/policy/agent-policy.json",
                        "asset_name": "agent-policy.json",
                        "target_path": "agent-policy.managed.json",
                        "inline_json": {"schema_version": "1.0", "managed_by": "ghdp", "allow": ["sync.read", "sync.mutate"]},
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    revise_result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="revise",
        asset_target="ghdp-agent-policy",
        payload_file=payload_path,
        provider_family="github_release",
    )
    assert "platform-cli/release-assets/content_index/content-index.json" in revise_result.changed_files
    assert "platform-cli/release-assets/catalog/ghdp_agent_policy.json" in revise_result.changed_files
    assert "platform-cli/src/platform_cli/resources/policy/agent-policy.json" in revise_result.changed_files
    assert revise_result.bundle_contract_path == "platform-cli/release-assets/catalog/ghdp_agent_policy.json"
    assert Path(revise_result.built_bundle_dir).exists()
    content_index = json.loads((repo_root / "platform-cli" / "release-assets" / "content_index" / "content-index.json").read_text(encoding="utf-8"))
    entry = next(item for item in content_index["capabilities"] if item["capability"] == "ghdp-agent-policy")
    assert entry["category"] == "repo_governance"


def test_asset_lifecycle_publishes_release_backed_capability_with_gh(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_asset_repo(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.runtime_support.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_asset_lifecycle._capability_inventory", lambda _repo_root: [])
    start_orchestrate_run(repo_root=repo_root)

    payload_path = repo_root / "publish_payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "capability": "ghdp-runtime-hints",
                "version": "0.200.0",
                "repo": "gh-org-data-platform/dp-tools-local-setup",
                "tag": "ghdp-runtime-hints-v0.200.0",
                "target_root_key": "ghdp_user_root",
                "target_subdir": "policies",
                "files": [
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/policy/runtime-hints.json",
                        "asset_name": "runtime-hints.json",
                        "target_path": "runtime-hints.managed.json",
                        "inline_json": {"schema_version": "1.0", "hints": ["alpha", "beta"]},
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def _fake_run_cmd(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        if cmd[:4] == ["gh", "release", "view", "ghdp-runtime-hints-v0.200.0"]:
            return type("Res", (), {"returncode": 1, "stdout": "", "stderr": "missing"})()
        if cmd[:4] == ["gh", "release", "view", "content-index-latest"]:
            return type("Res", (), {"returncode": 0, "stdout": "exists", "stderr": ""})()
        return type("Res", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("platform_cli.tools.orchestrate_asset_lifecycle.run_cmd", _fake_run_cmd)

    result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="create",
        payload_file=payload_path,
        provider_family="github_release",
        publish=True,
    )

    assert result.published is True
    assert any(cmd[:3] == ["gh", "release", "create"] and "ghdp-runtime-hints-v0.200.0" in cmd for cmd in calls)
    assert any(cmd[:3] == ["gh", "release", "upload"] and "ghdp-runtime-hints-v0.200.0" in cmd for cmd in calls)
    assert any(cmd[:3] == ["gh", "release", "upload"] and "content-index-latest" in cmd for cmd in calls)


def test_asset_lifecycle_updates_and_removes_generic_release_backed_capability(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_asset_repo(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.runtime_support.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_asset_lifecycle._capability_inventory", lambda _repo_root: [])
    start_orchestrate_run(repo_root=repo_root)

    payload_path = repo_root / "generic_payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "capability": "ghdp-versioned-asset",
                "version": "0.100.0",
                "repo": "gh-org-data-platform/dp-tools-local-setup",
                "tag": "ghdp-versioned-asset-v0.100.0",
                "target_root_key": "ghdp_user_root",
                "target_subdir": "policies",
                "files": [
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/policy/versioned-asset.json",
                        "asset_name": "versioned-asset.json",
                        "target_path": "versioned-asset.managed.json",
                        "inline_json": {"schema_version": "1.0", "version": "0.100.0"},
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    run_asset_lifecycle(
        repo_root=repo_root,
        operation="create",
        provider_family="github_release",
        payload_file=payload_path,
    )

    update_result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="update_versioned_asset",
        asset_target="ghdp-versioned-asset",
        provider_family="github_release",
        new_version="0.101.0",
    )
    assert "platform-cli/release-assets/catalog/ghdp_versioned_asset.json" in update_result.changed_files
    updated_contract = json.loads((repo_root / "platform-cli/release-assets/catalog/ghdp_versioned_asset.json").read_text(encoding="utf-8"))
    assert updated_contract["version"] == "0.101.0"
    assert updated_contract["tag"] == "ghdp-versioned-asset-v0.101.0"

    remove_result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="remove",
        asset_target="ghdp-versioned-asset",
        provider_family="github_release",
    )
    assert "platform-cli/release-assets/catalog/ghdp_versioned_asset.json" in remove_result.changed_files
    assert not (repo_root / "platform-cli/release-assets/catalog/ghdp_versioned_asset.json").exists()
    content_index = json.loads((repo_root / "platform-cli/release-assets/content_index/content-index.json").read_text(encoding="utf-8"))
    assert not any(item["capability"] == "ghdp-versioned-asset" for item in content_index["capabilities"])


def test_asset_lifecycle_revises_and_removes_marketplace_capability(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_asset_repo(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.runtime_support.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_asset_lifecycle._capability_inventory", lambda _repo_root: [])
    start_orchestrate_run(repo_root=repo_root)

    payload_path = repo_root / "marketplace_payload.json"
    payload_path.write_text(
        json.dumps({"target_name": "claude", "category": "plugin"}, indent=2) + "\n",
        encoding="utf-8",
    )
    revise_result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="revise",
        asset_target="marketplace-codex-skill-git-branch-review",
        payload_file=payload_path,
        provider_family="marketplace_repo",
    )
    assert revise_result.provider_family == "marketplace_repo"
    allowlist = json.loads((repo_root / ".ghdp" / "capability-allowlist.json").read_text(encoding="utf-8"))
    claude_entries = allowlist["sources"]["skill_marketplace"]["targets"]["claude"]["entries"]
    assert any(item["capability"] == "marketplace-codex-skill-git-branch-review" for item in claude_entries)

    remove_result = run_asset_lifecycle(
        repo_root=repo_root,
        operation="remove",
        asset_target="marketplace-codex-skill-git-branch-review",
        provider_family="marketplace_repo",
    )
    assert remove_result.changed_files == [".ghdp/capability-allowlist.json"]
    allowlist = json.loads((repo_root / ".ghdp" / "capability-allowlist.json").read_text(encoding="utf-8"))
    assert not any(
        item["capability"] == "marketplace-codex-skill-git-branch-review"
        for target in allowlist["sources"]["skill_marketplace"]["targets"].values()
        for item in target.get("entries", [])
    )
