from __future__ import annotations

import platform_cli.core.secure_defaults as secure_defaults


def test_runtime_default_keys_exclude_github_secret_config() -> None:
    assert "GHDP_TOKEN_SECRET_ID" not in secure_defaults.RUNTIME_DEFAULT_KEYS
    assert "GHDP_GITHUB_SECRET_ID" not in secure_defaults.RUNTIME_DEFAULT_KEYS
    assert "GHDP_GITHUB_SECRET_REGION" not in secure_defaults.RUNTIME_DEFAULT_KEYS
    assert "GHDP_INSTALL_FLAVOR" in secure_defaults.RUNTIME_DEFAULT_KEYS
