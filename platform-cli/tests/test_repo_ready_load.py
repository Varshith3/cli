from __future__ import annotations

from pathlib import Path

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.repo_ready_load import (
    load_repo_ready_prompt,
    load_repo_ready_template,
    load_repo_ready_vocab,
)


def test_load_repo_ready_prompt_raises_when_global_assets_are_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home_root = tmp_path / "ghdp-home-missing"
    monkeypatch.setenv("HOME", str(home_root))
    monkeypatch.setenv("USERPROFILE", str(home_root))
    with pytest.raises(PlatformError) as err:
        load_repo_ready_prompt("config_yaml_generation.md")

    assert err.value.code == "E_REPO_READY_PROMPT_MISSING"


def test_load_repo_ready_assets_use_global_shared_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home_root = tmp_path / "ghdp-home"
    monkeypatch.setenv("HOME", str(home_root))
    monkeypatch.setenv("USERPROFILE", str(home_root))
    asset_root = home_root / ".ghdp" / "repo_ready" / "base"
    (asset_root / "prompts").mkdir(parents=True)
    (asset_root / "templates" / "ghdp").mkdir(parents=True)

    (asset_root / "prompts" / "config_yaml_generation.md").write_text("shared prompt", encoding="utf-8")
    (asset_root / "templates" / "ghdp" / "config.yaml").write_text("shared template", encoding="utf-8")
    (asset_root / "vocab.yaml").write_text('schema_version: "1.0"\norigin: shared\n', encoding="utf-8")

    assert load_repo_ready_prompt("config_yaml_generation.md") == "shared prompt"
    assert load_repo_ready_template("ghdp/config.yaml") == "shared template"
    assert load_repo_ready_vocab()["origin"] == "shared"
