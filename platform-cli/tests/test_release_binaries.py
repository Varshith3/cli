from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from platform_cli import cli as root_cli
from platform_cli.commands import release as release_cmd
from platform_cli.exec.runner import CmdResult
from platform_cli.tools.release import metadata, planner
from platform_cli.tools.release import executor as release_executor
from platform_cli.tools.release import workflow_adapter
from platform_cli.tools.release.executor import _prepare_asset
from platform_cli.tools.release.models import BuildTarget, ReleasePlan


def _result(cmd: list[str], stdout: str = "", returncode: int = 0, stderr: str = "") -> CmdResult:
    return CmdResult(cmd=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


def _seed_release_workdir(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "ghdp.spec").write_text("spec", encoding="utf-8")
    (workdir / "pyproject.toml").write_text("[build-system]\nrequires = []\n", encoding="utf-8")


def test_plan_binaries_feature_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)

    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.0")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    plan = planner.plan_binaries_release(repo_root=repo_root)

    assert plan.tag == "v0.2.1-CliReleaseManagementIntegration"
    assert plan.latest_stable_tag == "v0.2.0"
    assert plan.next_stable_tag == "v0.2.1"
    assert plan.prerelease is True
    assert plan.draft is False
    assert plan.build_target.asset == "ghdp-linux-amd64"


def test_plan_binaries_stable_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)

    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v1.0.1")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    plan = planner.plan_binaries_release(
        repo_root=repo_root,
        source_ref="develop",
        release_channel="auto",
    )

    assert plan.tag == "v1.0.2"
    assert plan.prerelease is False
    assert plan.is_stable_branch is True


def test_plan_binaries_stable_branch_allows_version_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)

    policy = planner.load_manual_build_policy()
    stable_branch = policy.stable_branches[0]

    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v1.0.1")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    plan = planner.plan_binaries_release(
        repo_root=repo_root,
        source_ref=stable_branch,
        version_override="0.9.9",
    )

    assert plan.tag == "v0.9.9"
    assert plan.version_override == "v0.9.9"


def test_plan_binaries_feature_branch_rejects_version_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)

    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.3")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    with pytest.raises(planner.PlatformError) as exc_info:
        planner.plan_binaries_release(repo_root=repo_root, version_override="v0.9.9")

    assert exc_info.value.code == "E_RELEASE_VERSION_OVERRIDE_UNSUPPORTED"


def test_plan_binaries_rejects_python_below_310(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)

    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v1.0.1")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    with pytest.raises(planner.PlatformError) as exc_info:
        planner.plan_binaries_release(
            repo_root=repo_root,
            source_ref="develop",
            python_version="3.9",
        )

    assert exc_info.value.code == "E_RELEASE_PYTHON_VERSION_UNSUPPORTED"


def test_plan_binaries_auto_detects_repo_standard_workdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)

    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.3")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    plan = planner.plan_binaries_release(repo_root=repo_root)

    assert plan.workdir == workdir.resolve()


def test_plan_binaries_auto_detects_when_invoked_from_package_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)
    (repo_root / ".git").mkdir(parents=True)
    (repo_root / ".github").mkdir(parents=True)

    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.3")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    plan = planner.plan_binaries_release(repo_root=workdir)

    assert plan.repo_root == repo_root.resolve()
    assert plan.workdir == workdir.resolve()


def test_plan_binaries_allows_explicit_workdir_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    override = repo_root / "custom-release-root"
    _seed_release_workdir(override)

    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.3")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    plan = planner.plan_binaries_release(repo_root=repo_root, workdir="custom-release-root")

    assert plan.workdir == override.resolve()


def test_plan_binaries_resolves_repo_relative_override_from_package_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)
    (repo_root / ".git").mkdir(parents=True)
    (repo_root / ".github").mkdir(parents=True)

    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.3")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    plan = planner.plan_binaries_release(repo_root=workdir, workdir="platform-cli")

    assert plan.repo_root == repo_root.resolve()
    assert plan.workdir == workdir.resolve()


def test_plan_binaries_errors_when_auto_detect_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    first = repo_root / "alpha-release"
    second = repo_root / "beta-release"
    _seed_release_workdir(first)
    _seed_release_workdir(second)

    policy = planner.load_manual_build_policy()
    monkeypatch.setattr(planner, "load_manual_build_policy", lambda: replace(policy, workdir_default="missing"))
    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.3")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    with pytest.raises(planner.PlatformError) as exc_info:
        planner.plan_binaries_release(repo_root=repo_root)

    assert exc_info.value.code == "E_RELEASE_WORKDIR_AMBIGUOUS"


def test_plan_binaries_errors_when_auto_detect_finds_no_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    policy = planner.load_manual_build_policy()
    monkeypatch.setattr(planner, "load_manual_build_policy", lambda: replace(policy, workdir_default="missing"))
    monkeypatch.setattr(planner, "get_current_branch", lambda _repo_root: "feature/EPPE-7087-TECHNICAL-cli-release-management-integration")
    monkeypatch.setattr(planner, "get_latest_stable_anchor", lambda repo: "v0.2.3")
    monkeypatch.setattr(planner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(planner.platform, "machine", lambda: "x86_64")

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return _result(cmd, stdout='{"nameWithOwner":"gh-org-data-platform/dp-tools-local-setup"}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    with pytest.raises(planner.PlatformError) as exc_info:
        planner.plan_binaries_release(repo_root=repo_root)

    assert exc_info.value.code == "E_RELEASE_WORKDIR_MISSING"


def test_validate_release_notes_freshness_for_feature_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    summary_file.parent.mkdir(parents=True)
    _seed_release_workdir(workdir)
    summary_file.write_text("### Summary\n- release note\n", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="standard",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.3",
        next_stable_tag="v0.2.4",
        tag="v0.2.4-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.4-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    def fake_run_cmd(cmd, **kwargs):
        if cmd[:2] == ["git", "fetch"]:
            return _result(cmd)
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return _result(cmd, returncode=0, stdout="abc123\n")
        if cmd[:2] == ["git", "merge-base"]:
            return _result(cmd, stdout="abc123")
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _result(cmd, stdout=".github/release-notes/notes.md\nREADME.md\n")
        if cmd[:3] == ["git", "rev-list", "--max-count=4"]:
            return _result(cmd, stdout="c1\nc2\n")
        if cmd[:3] == ["git", "diff-tree", "--no-commit-id"]:
            if cmd[-1] == "c1":
                return _result(cmd, stdout=".github/release-notes/notes.md\n")
            return _result(cmd, stdout="README.md\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)

    planner.validate_release_notes_freshness(plan)


def test_release_notes_base_ref_falls_back_to_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    def fake_run_cmd(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--verify", "origin/develop"]:
            return _result(cmd, returncode=1, stderr="missing")
        if cmd == ["git", "rev-parse", "--verify", "origin/main"]:
            return _result(cmd, returncode=0, stdout="abc\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(planner, "run_cmd", fake_run_cmd)
    assert planner._resolve_release_notes_base_ref(repo_root) == "origin/main"


def test_render_release_notes_and_metadata_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    summary_file.parent.mkdir(parents=True)
    _seed_release_workdir(workdir)
    (workdir / "src" / "platform_cli").mkdir(parents=True)
    summary_file.write_text("### Summary\n- change one\n- change two\n", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="standard",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.3",
        next_stable_tag="v0.2.4",
        tag="v0.2.4-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.4-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    monkeypatch.setenv("GHDP_DEFAULT_REPO", "gh-org-data-platform/dp-tools-local-setup")
    monkeypatch.setenv("GHDP_AWS_REGION", "us-east-1")

    notes = metadata.render_release_notes(plan)
    metadata.write_build_metadata(plan)
    metadata.write_runtime_defaults(plan)

    assert "## Summary" in notes
    assert "- change one" in notes
    assert 'BUILD_TAG = "v0.2.4-CliReleaseManagementIntegration"' in plan.build_meta_path.read_text(encoding="utf-8")
    assert 'BUILD_INSTALL_FLAVOR = "standard"' in plan.build_meta_path.read_text(encoding="utf-8")
    runtime_defaults = plan.runtime_defaults_path.read_text(encoding="utf-8")
    assert '"GHDP_DEFAULT_REPO": "gh-org-data-platform/dp-tools-local-setup"' in runtime_defaults
    assert '"GHDP_AWS_REGION": "us-east-1"' in runtime_defaults
    assert '"GHDP_INSTALL_FLAVOR": "standard"' in runtime_defaults
    assert "GHDP_TOKEN_SECRET_ID" not in runtime_defaults
    assert "GHDP_GITHUB_SECRET_ID" not in runtime_defaults
    assert "GHDP_GITHUB_SECRET_REGION" not in runtime_defaults


def test_render_release_notes_empty_summary_has_actionable_message(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    summary_file.parent.mkdir(parents=True)
    _seed_release_workdir(workdir)
    summary_file.write_text("### Summary\n", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        workdir=workdir,
        install_flavor="standard",
        python_version="3.11",
        latest_stable_tag="v0.2.3",
        next_stable_tag="v0.2.4",
        tag="v0.2.4-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.4-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    with pytest.raises(metadata.PlatformError) as exc_info:
        metadata.render_release_notes(plan)

    assert exc_info.value.code == "E_RELEASE_NOTES_EMPTY"
    assert "could not render release notes" in str(exc_info.value)
    assert "Add a human-readable release summary" in str(exc_info.value)


def test_install_script_does_not_run_post_install_scheduler_setup() -> None:
    script_path = Path(__file__).resolve().parents[1] / "install_ghdp.sh"
    script = script_path.read_text(encoding="utf-8")

    assert '_post-install-scheduler-setup' not in script
    assert "Retry with: ghdp schedule apply" not in script


def test_prepare_asset_copies_expected_built_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    built_file = workdir / "dist" / "ghdp"
    built_file.parent.mkdir(parents=True)
    built_file.write_text("binary-content", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="standard",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.3",
        next_stable_tag="v0.2.4",
        tag="v0.2.4-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=repo_root / ".github" / "release-notes" / "notes.md",
        template_file=repo_root / ".github" / "release-notes" / "template.md",
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.4-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    asset_path, checksum_path = _prepare_asset(plan)

    assert asset_path.name == "ghdp-linux-amd64"
    assert checksum_path.name == "ghdp-linux-amd64.sha256"
    assert asset_path.read_text(encoding="utf-8") == "binary-content"


def test_build_binaries_managed_embeds_token_in_build_meta_and_uploads_binary_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    built_file = workdir / "dist" / "ghdp"
    built_file.parent.mkdir(parents=True)
    built_file.write_text("binary-content", encoding="utf-8")
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("summary", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="managed",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.3",
        next_stable_tag="v0.2.4",
        tag="v0.2.4-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.4-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    uploads: list[list[str]] = []
    monkeypatch.setattr(release_executor, "ensure_binaries_release", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "_install_build_dependencies", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "_run_pyinstaller", lambda release_plan: None)

    def fake_run_cmd(cmd, **kwargs):
        uploads.append(list(cmd))
        return _result(cmd)

    monkeypatch.setattr(release_executor, "run_cmd", fake_run_cmd)

    monkeypatch.setenv("GHDP_MANAGED_GITHUB_TOKEN", "ghp_test_managed_token")

    result = release_executor.build_binaries_for_current_platform(plan)

    assert result.install_flavor == "managed"
    build_meta = plan.build_meta_path.read_text(encoding="utf-8")
    assert 'BUILD_INSTALL_FLAVOR = "managed"' in build_meta
    assert 'BUILD_MANAGED_GITHUB_TOKEN = "ghp_test_managed_token"' in build_meta
    assert uploads and uploads[0][:3] == ["gh", "release", "upload"]
    assert str(result.asset_path) in uploads[0]
    assert str(result.checksum_path) in uploads[0]
    assert all("managed-install.json" not in part for part in uploads[0])


def test_build_binaries_managed_requires_token_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    built_file = workdir / "dist" / "ghdp"
    built_file.parent.mkdir(parents=True)
    built_file.write_text("binary-content", encoding="utf-8")
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("summary", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="managed",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.3",
        next_stable_tag="v0.2.4",
        tag="v0.2.4-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.4-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    monkeypatch.setattr(release_executor, "ensure_binaries_release", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "_install_build_dependencies", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "_run_pyinstaller", lambda release_plan: None)
    monkeypatch.delenv("GHDP_MANAGED_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GHDP_MANAGED_INSTALL_TOKEN", raising=False)
    monkeypatch.delenv("GHDP_MANAGED_TOKEN", raising=False)
    monkeypatch.setattr(release_executor, "run_cmd", lambda cmd, **kwargs: _result(cmd))

    with pytest.raises(release_executor.PlatformError) as exc_info:
        release_executor.build_binaries_for_current_platform(plan)
    assert exc_info.value.code == "E_RELEASE_MANAGED_TOKEN_REQUIRED"


def test_build_binaries_managed_embeds_token_in_build_meta_when_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    built_file = workdir / "dist" / "ghdp"
    built_file.parent.mkdir(parents=True)
    built_file.write_text("binary-content", encoding="utf-8")
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("summary", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="managed",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.3",
        next_stable_tag="v0.2.4",
        tag="v0.2.4-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.4-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    monkeypatch.setattr(release_executor, "ensure_binaries_release", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "_install_build_dependencies", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "_run_pyinstaller", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "run_cmd", lambda cmd, **kwargs: _result(cmd))
    monkeypatch.setenv("GHDP_MANAGED_GITHUB_TOKEN", "ghp_test_managed_token")

    result = release_executor.build_binaries_for_current_platform(plan)

    payload = plan.build_meta_path.read_text(encoding="utf-8")
    assert 'BUILD_MANAGED_GITHUB_TOKEN = "ghp_test_managed_token"' in payload


def test_release_plan_json_output_is_machine_readable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    _seed_release_workdir(workdir)
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("summary", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    monkeypatch.setattr(root_cli, "maybe_check_for_update", lambda force=False: False)
    monkeypatch.setattr(
        release_cmd,
        "plan_binaries_release",
        lambda **kwargs: ReleasePlan(
            repo_root=repo_root,
            repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
            source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
            install_flavor="standard",
            workdir=workdir,
            python_version="3.11",
            latest_stable_tag="v0.2.0",
            next_stable_tag="v0.2.1",
            tag="v0.2.1-CliReleaseManagementIntegration",
            ticket="EPPE-7087",
            feature_slug="CliReleaseManagementIntegration",
            is_stable_branch=False,
            draft=False,
            prerelease=True,
            summary_file=summary_file,
            template_file=template_file,
            build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
            runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
            build_version="0.2.1-CliReleaseManagementIntegration",
            build_channel="beta",
            build_target=BuildTarget(system="windows", machine="x86_64", asset="ghdp-windows-amd64.exe", built_path="dist/ghdp.exe"),
        ),
    )

    result = runner.invoke(root_cli.app, ["--json", "release", "plan-binaries"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["tag"] == "v0.2.1-CliReleaseManagementIntegration"
    assert payload["install_flavor"] == "standard"


def test_ensure_binaries_release_creates_missing_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    _seed_release_workdir(workdir)
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("summary", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="standard",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.0",
        next_stable_tag="v0.2.1",
        tag="v0.2.1-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.1-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="windows", machine="x86_64", asset="ghdp-windows-amd64.exe", built_path="dist/ghdp.exe"),
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr(release_executor, "_ensure_gh_authenticated", lambda: None)
    monkeypatch.setattr(release_executor, "validate_release_notes_freshness", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "render_release_notes", lambda release_plan: "notes")
    monkeypatch.setattr(release_executor, "_ensure_tag_ref", lambda release_plan: None)
    monkeypatch.setattr(release_executor, "_find_release_id", lambda release_plan: None)
    monkeypatch.setattr(
        release_executor,
        "_create_release",
        lambda *, plan, notes_path: captured.update({"tag": plan.tag, "notes_exists": notes_path.exists()}),
    )

    result = release_executor.ensure_binaries_release(plan)

    assert result["tag"] == plan.tag
    assert captured == {"tag": plan.tag, "notes_exists": True}


def test_install_build_dependencies_only_installs_runtime_prereqs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    _seed_release_workdir(workdir)

    plan = ReleasePlan(
        repo_root=repo_root,
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        install_flavor="standard",
        workdir=workdir,
        python_version="3.11",
        latest_stable_tag="v0.2.0",
        next_stable_tag="v0.2.1",
        tag="v0.2.1-CliReleaseManagementIntegration",
        ticket="EPPE-7087",
        feature_slug="CliReleaseManagementIntegration",
        is_stable_branch=False,
        draft=False,
        prerelease=True,
        summary_file=repo_root / ".github" / "release-notes" / "notes.md",
        template_file=repo_root / ".github" / "release-notes" / "template.md",
        build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.1-CliReleaseManagementIntegration",
        build_channel="beta",
        build_target=BuildTarget(system="windows", machine="x86_64", asset="ghdp-windows-amd64.exe", built_path="dist/ghdp.exe"),
    )

    calls: list[list[str]] = []

    def fake_run_cmd(cmd, **kwargs):
        calls.append(list(cmd))
        return _result(cmd)

    monkeypatch.setattr(release_executor, "run_cmd", fake_run_cmd)

    release_executor._install_build_dependencies(plan)

    assert calls == [
        [release_executor.sys.executable, "-m", "pip", "install", "-U", "pip"],
        [release_executor.sys.executable, "-m", "pip", "install", "pyinstaller"],
    ]


def test_prepare_release_writes_github_outputs_when_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    repo_root = tmp_path / "repo"
    workdir = repo_root / "platform-cli"
    summary_file = repo_root / ".github" / "release-notes" / "notes.md"
    template_file = repo_root / ".github" / "release-notes" / "template.md"
    output_file = tmp_path / "github_output.txt"
    _seed_release_workdir(workdir)
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("summary", encoding="utf-8")
    template_file.write_text("template", encoding="utf-8")

    monkeypatch.setattr(root_cli, "maybe_check_for_update", lambda force=False: False)
    monkeypatch.setattr(
        release_cmd,
        "plan_binaries_release",
        lambda **kwargs: ReleasePlan(
            repo_root=repo_root,
            repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
            source_ref="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
            install_flavor="standard",
            workdir=workdir,
            python_version="3.11",
            latest_stable_tag="v0.2.0",
            next_stable_tag="v0.2.1",
            tag="v0.2.1-CliReleaseManagementIntegration",
            ticket="EPPE-7087",
            feature_slug="CliReleaseManagementIntegration",
            is_stable_branch=False,
            draft=False,
            prerelease=True,
            summary_file=summary_file,
            template_file=template_file,
            build_meta_path=workdir / "src" / "platform_cli" / "_build_meta.py",
            runtime_defaults_path=workdir / "src" / "platform_cli" / "_runtime_defaults.py",
            build_version="0.2.1-CliReleaseManagementIntegration",
            build_channel="beta",
            build_target=BuildTarget(system="windows", machine="x86_64", asset="ghdp-windows-amd64.exe", built_path="dist/ghdp.exe"),
        ),
    )
    monkeypatch.setattr(
        release_cmd,
        "ensure_binaries_release",
        lambda plan: {
            "tag": plan.tag,
            "draft": plan.draft,
            "prerelease": plan.prerelease,
            "source_ref": plan.source_ref,
            "release_repo": plan.repo_name_with_owner,
        },
    )

    result = runner.invoke(
        root_cli.app,
        ["release", "prepare-binaries-release", "--install-flavor", "managed"],
        env={"GITHUB_OUTPUT": str(output_file)},
    )

    assert result.exit_code == 0
    output = output_file.read_text(encoding="utf-8")
    assert "tag=v0.2.1-CliReleaseManagementIntegration" in output
    assert "script_ref=feature/EPPE-7087-TECHNICAL-cli-release-management-integration" in output
    assert "install_flavor=standard" in output
    assert "is_stable=false" in output
    assert "prerelease=true" in output


def test_manual_build_workflow_removes_dead_optional_inputs_and_uses_version_override() -> None:
    workflow_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "manual-build-binaries.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workdir:" not in workflow
    assert "install_flavor:" in workflow
    assert "aws_role_arn:" not in workflow
    assert "python_version:" in workflow
    assert "inputs.workdir" not in workflow
    assert "inputs.install_flavor" in workflow
    assert "inputs.aws_role_arn" not in workflow
    assert "GHDP_INSTALL_FLAVOR" in workflow
    assert "version_override:" in workflow
    assert "--version-override" in workflow
    assert 'python-version: "3.10"' in workflow
    assert '--python-version "${{ inputs.python_version }}"' in workflow
    assert '--version-override "${{ needs.prepare-release.outputs.tag }}"' in workflow


def test_workflow_adapter_noops_without_github_output(tmp_path: Path) -> None:
    plan = ReleasePlan(
        repo_root=tmp_path / "repo",
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        source_ref="develop",
        install_flavor="standard",
        workdir=tmp_path / "repo" / "platform-cli",
        python_version="3.11",
        latest_stable_tag="v0.2.0",
        next_stable_tag="v0.2.1",
        tag="v0.2.1",
        ticket="",
        feature_slug="",
        is_stable_branch=True,
        draft=False,
        prerelease=False,
        summary_file=tmp_path / "repo" / ".github" / "release-notes" / "notes.md",
        template_file=tmp_path / "repo" / ".github" / "release-notes" / "template.md",
        build_meta_path=tmp_path / "repo" / "platform-cli" / "src" / "platform_cli" / "_build_meta.py",
        runtime_defaults_path=tmp_path / "repo" / "platform-cli" / "src" / "platform_cli" / "_runtime_defaults.py",
        build_version="0.2.1",
        build_channel="stable",
        build_target=BuildTarget(system="linux", machine="x86_64", asset="ghdp-linux-amd64", built_path="dist/ghdp"),
    )

    assert workflow_adapter.write_prepare_outputs_if_supported(plan) is False
