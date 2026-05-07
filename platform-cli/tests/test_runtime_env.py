from pathlib import Path

from platform_cli.core import runtime_env as runtime_env_mod


def test_runtime_env_path_prefers_explicit_override(monkeypatch, tmp_path):
    override = tmp_path / "custom.env"
    monkeypatch.setenv("GHDP_RUNTIME_ENV_PATH", str(override))

    resolved = runtime_env_mod.runtime_env_path()

    assert resolved == override


def test_runtime_env_path_uses_repo_dotenv(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    nested = repo_root / "platform-cli" / "src"
    nested.mkdir(parents=True)
    (repo_root / ".git").mkdir()
    repo_env = repo_root / "platform-cli" / ".env"
    repo_env.write_text("GHDP_DEFAULT_REPO=test/repo\n", encoding="utf-8")

    monkeypatch.delenv("GHDP_RUNTIME_ENV_PATH", raising=False)
    assert runtime_env_mod._find_repo_runtime_env(nested) == repo_env

    # Route runtime_env_path through the repo discovery helper without depending on cwd.
    monkeypatch.setattr(runtime_env_mod, "_find_repo_runtime_env", lambda start=None: repo_env)
    assert runtime_env_mod.runtime_env_path() == repo_env


def test_runtime_env_path_falls_back_to_home_default_outside_repo(monkeypatch, tmp_path):
    workdir = tmp_path / "outside"
    workdir.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.delenv("GHDP_RUNTIME_ENV_PATH", raising=False)
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(runtime_env_mod.Path, "home", staticmethod(lambda: home))

    resolved = runtime_env_mod.runtime_env_path()

    assert resolved == home / ".ghdp" / "runtime.env"
