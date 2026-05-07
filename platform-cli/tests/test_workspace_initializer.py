from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.repo_structure import AppConfig, InfraStackConfig, RepoStructure
from platform_cli.tools.workspace_initializer import init_workspace


def _ok(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_init_workspace_syncs_apps_and_refreshes_infra(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    (repo_root / "apps" / "py-app").mkdir(parents=True, exist_ok=True)
    (repo_root / "apps" / "py-app" / "pyproject.toml").write_text("[project]\nname='py-app'\n", encoding="utf-8")
    (repo_root / "apps" / "scala-app").mkdir(parents=True, exist_ok=True)
    (repo_root / "apps" / "scala-app" / "pom.xml").write_text("<project/>", encoding="utf-8")
    (repo_root / "infra" / "default").mkdir(parents=True, exist_ok=True)

    repo = RepoStructure(
        repo_root=str(repo_root),
        apps=[
            AppConfig(path="py-app", tools=["uv"]),
            AppConfig(path="scala-app", tools=["maven"]),
        ],
        infra_stacks=[InfraStackConfig(id="default", path="default", deployment_order=1)],
        infra_templates_version="v1.2.3",
    )

    commands: list[list[str]] = []

    def run_cmd_stub(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        commands.append(list(cmd))
        if cmd[:2] == ["uv", "--version"]:
            return _ok("uv 0.8.0")
        if cmd[:2] == ["uv", "sync"]:
            return _ok()
        if cmd[:2] == ["mvn", "--version"]:
            return _ok("Apache Maven 3.9")
        if cmd[:2] == ["mvn", "dependency:go-offline"]:
            return _ok()
        if cmd[:2] == ["git", "ls-remote"]:
            return _ok("abcd\trefs/tags/v1.2.3")
        return _ok()

    dep_calls: list[tuple[Path, list[dict], bool]] = []

    def ensure_deps_stub(tf_root, dependencies, refresh_deps=False, rich_logs=False):  # type: ignore[no-untyped-def]
        dep_calls.append((tf_root, list(dependencies), refresh_deps))
        return []

    monkeypatch.setattr("platform_cli.tools.workspace_initializer.run_cmd", run_cmd_stub)
    monkeypatch.setattr("platform_cli.tools.workspace_initializer.ensure_deps", ensure_deps_stub)
    monkeypatch.setattr(
        "platform_cli.tools.workspace_initializer.get_infra_templates_repo",
        lambda: "gh-org-data-platform/terraform-aws-gh-dp-infra-templates",
    )

    result = init_workspace(
        repo=repo,
        repo_root=repo_root,
        app_name=None,
        context={"verbose": False, "quiet": False},
    )

    assert result.initialized_apps == ["py-app", "scala-app"]
    assert result.refreshed_stacks == ["default"]
    assert ["uv", "sync"] in commands
    assert ["mvn", "dependency:go-offline", "-DskipTests"] in commands
    assert dep_calls[0][0] == repo_root / "infra" / "default"
    assert dep_calls[0][2] is True
    assert dep_calls[0][1][0]["ref"] == "v1.2.3"


def test_init_workspace_honors_single_app_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    (repo_root / "apps" / "py-app").mkdir(parents=True, exist_ok=True)
    (repo_root / "apps" / "py-app" / "pyproject.toml").write_text("[project]\nname='py-app'\n", encoding="utf-8")
    (repo_root / "apps" / "scala-app").mkdir(parents=True, exist_ok=True)
    (repo_root / "apps" / "scala-app" / "pom.xml").write_text("<project/>", encoding="utf-8")

    repo = RepoStructure(
        repo_root=str(repo_root),
        apps=[
            AppConfig(path="py-app", tools=["uv"]),
            AppConfig(path="scala-app", tools=["maven"]),
        ],
        infra_stacks=[],
        infra_templates_version="",
    )

    commands: list[list[str]] = []

    def run_cmd_stub(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        commands.append(list(cmd))
        if cmd[:2] == ["uv", "--version"]:
            return _ok("uv 0.8.0")
        if cmd[:2] == ["uv", "sync"]:
            return _ok()
        return _ok()

    monkeypatch.setattr("platform_cli.tools.workspace_initializer.run_cmd", run_cmd_stub)

    result = init_workspace(
        repo=repo,
        repo_root=repo_root,
        app_name="py-app",
        context={"verbose": False, "quiet": False},
    )

    assert result.initialized_apps == ["py-app"]
    assert ["uv", "sync"] in commands
    assert ["mvn", "dependency:go-offline", "-DskipTests"] not in commands


def test_init_workspace_requires_infra_templates_version(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    (repo_root / "infra" / "default").mkdir(parents=True, exist_ok=True)
    repo = RepoStructure(
        repo_root=str(repo_root),
        apps=[],
        infra_stacks=[InfraStackConfig(id="default", path="default", deployment_order=1)],
        infra_templates_version="",
    )

    with pytest.raises(PlatformError) as exc:
        init_workspace(
            repo=repo,
            repo_root=repo_root,
            app_name=None,
            context={"verbose": False, "quiet": False},
        )

    assert exc.value.code == "E_MISSING_INFRA_TEMPLATES_VERSION"


def test_init_workspace_rejects_invalid_infra_templates_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    (repo_root / "infra" / "default").mkdir(parents=True, exist_ok=True)
    repo = RepoStructure(
        repo_root=str(repo_root),
        apps=[],
        infra_stacks=[InfraStackConfig(id="default", path="default", deployment_order=1)],
        infra_templates_version="does-not-exist",
    )

    def run_cmd_stub(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        if cmd[:2] == ["git", "ls-remote"]:
            return SimpleNamespace(returncode=2, stdout="", stderr="fatal")
        return _ok()

    monkeypatch.setattr("platform_cli.tools.workspace_initializer.run_cmd", run_cmd_stub)
    monkeypatch.setattr(
        "platform_cli.tools.workspace_initializer.get_infra_templates_repo",
        lambda: "gh-org-data-platform/terraform-aws-gh-dp-infra-templates",
    )

    with pytest.raises(PlatformError) as exc:
        init_workspace(
            repo=repo,
            repo_root=repo_root,
            app_name=None,
            context={"verbose": False, "quiet": False},
        )

    assert exc.value.code == "E_INVALID_INFRA_TEMPLATES_VERSION"
