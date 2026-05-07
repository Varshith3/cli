from platform_cli.core.context import ctx as cli_ctx
from platform_cli.tools import aws_profile


def test_resolve_prompts_when_flag_missing(monkeypatch):
    cli_ctx.non_interactive = False
    monkeypatch.setattr(aws_profile, "_repo_key", lambda: "")
    monkeypatch.setattr(aws_profile, "prompt_aws_profile_choice", lambda default_profile="default": "picked-profile")
    persisted = []
    monkeypatch.setattr(aws_profile, "set_active_profile", lambda profile, scope="global": persisted.append((profile, scope)))

    resolved = aws_profile.resolve_aws_profile(
        explicit_profile=None,
        prompt_if_unresolved=False,
        prompt_when_flag_missing=True,
        persist_prompt_scope="global",
    )

    assert resolved.profile == "picked-profile"
    assert resolved.source == "prompt"
    assert persisted == [("picked-profile", "global")]


def test_resolve_does_not_prompt_when_flag_provided(monkeypatch):
    cli_ctx.non_interactive = False
    monkeypatch.setattr(aws_profile, "_repo_key", lambda: "")

    resolved = aws_profile.resolve_aws_profile(
        explicit_profile="explicit-profile",
        prompt_if_unresolved=False,
        prompt_when_flag_missing=True,
    )

    assert resolved.profile == "explicit-profile"
    assert resolved.source == "flag"


def test_resolve_non_interactive_skips_picker_and_uses_env(monkeypatch):
    cli_ctx.non_interactive = True
    monkeypatch.setattr(aws_profile, "_repo_key", lambda: "")
    monkeypatch.setenv("AWS_PROFILE", "env-profile")

    resolved = aws_profile.resolve_aws_profile(
        explicit_profile=None,
        prompt_if_unresolved=False,
        prompt_when_flag_missing=True,
    )

    assert resolved.profile == "env-profile"
    assert resolved.source == "env"


def test_set_active_profile_global_syncs_aws_profile_env(monkeypatch):
    applied = []
    monkeypatch.setattr(aws_profile, "set_global_active_profile", lambda profile: applied.append(("global-store", profile)))
    monkeypatch.setattr(aws_profile, "apply_active_profile_env", lambda profile, scope="global": applied.append(("env", profile, scope)))

    scope = aws_profile.set_active_profile("data-engg-md", scope="global")

    assert scope == "global"
    assert applied == [("global-store", "data-engg-md"), ("env", "data-engg-md", "global")]


def test_set_active_profile_repo_only_sets_process_env(monkeypatch):
    applied = []
    monkeypatch.setattr(aws_profile, "_repo_key", lambda: "repo-key")
    monkeypatch.setattr(aws_profile, "_set_repo_active_profile", lambda repo_key, profile: applied.append(("repo-store", repo_key, profile)))
    monkeypatch.setattr(aws_profile, "apply_active_profile_env", lambda profile, scope="global": applied.append(("env", profile, scope)))

    scope = aws_profile.set_active_profile("repo-profile", scope="repo")

    assert scope == "repo"
    assert applied == [("repo-store", "repo-key", "repo-profile"), ("env", "repo-profile", "repo")]
