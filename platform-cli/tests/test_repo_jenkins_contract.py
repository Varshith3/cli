from __future__ import annotations

import json
from pathlib import Path
import importlib

repo_jenkins_contract = importlib.import_module("platform_cli.tools.repo_jenkins_contract")


def test_build_repo_jenkins_contract_extracts_parameter_schema(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    jenkinsfile = """
pipeline {
    parameters {
        choice(
            name: 'TARGET_WORKSPACE',
            choices: getEnvironmentList(account:account_name),
            description: 'Infra deployment workspace'
        )
        booleanParam(name: 'APPLY', defaultValue: false, description: 'Apply infra')
        string(name: 'APPLICATION_NAME', defaultValue: 'demo', description: 'Application name')
    }
    stages {
        stage("Validate & Plan") {
            steps {
                validateAndPlan(account:"x", env:"dev")
            }
        }
    }
}
""".strip()

    payload = repo_jenkins_contract.build_repo_jenkins_contract(
        repo_root=repo_dir,
        branch_name="develop",
        jenkins_text=jenkinsfile,
    )

    assert payload["schema_version"] == "2.0"
    assert payload["policy_version"] == "2026-04-16"
    assert payload["pipeline"]["style"] == "repo_local_pipeline"
    assert payload["pipeline"]["stage_names"] == ["Validate & Plan"]
    assert payload["pipeline"]["helper_calls"] == ["validateAndPlan"]
    assert payload["release_surface"]["indexed_choices"][0]["flow"] == "feature_to_dev"
    assert payload["flows"]["feature_to_dev"]["job_path"].startswith("job/UDP/")
    assert payload["flows"]["feature_to_dev"]["job_path"].endswith("1-promote-feature-to-dev")
    assert payload["flows"]["make_release"]["job_path"].startswith("job/UDP/")
    assert payload["flows"]["make_release"]["job_path"].endswith("2-make-release")
    assert payload["auth"]["local_user_config_secret_storage"] is True
    assert payload["comparison"]["fixture_root"] == "platform-cli/tests/fixtures/release_migration/v1"
    schema = {item["name"]: item for item in payload["parameter_schema"]}
    assert schema["TARGET_WORKSPACE"]["kind"] == "choice"
    assert schema["TARGET_WORKSPACE"]["required"] is True
    assert schema["TARGET_WORKSPACE"]["choices_source"] == "getEnvironmentList(account:account_name)"
    assert schema["APPLY"]["default"] is False
    assert schema["APPLICATION_NAME"]["default"] == "demo"


def test_ensure_repo_jenkins_contract_creates_and_inspects_fresh_contract(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    (repo_dir / "Jenkinsfile").write_text(
        """
pipeline {
    parameters {
        booleanParam(name: 'APPLY', defaultValue: false, description: 'Apply infra')
    }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo_dir)

    result = repo_jenkins_contract.ensure_repo_jenkins_contract(repo_dir, refresh=False)
    inspection = repo_jenkins_contract.inspect_repo_jenkins_contract(repo_dir)

    assert result.status == "created"
    assert inspection.exists is True
    assert inspection.fresh is True
    contract_path = repo_dir / ".ghdp" / "ci" / "jenkins_contract.json"
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    assert payload["repo_name"] == "sample-repo"
    assert payload["schema_version"] == "2.0"


def test_inspect_repo_jenkins_contract_reports_stale_contract(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    jenkinsfile_path = repo_dir / "Jenkinsfile"
    jenkinsfile_path.write_text(
        """
pipeline {
    parameters {
        booleanParam(name: 'APPLY', defaultValue: false, description: 'Apply infra')
    }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo_dir)
    repo_jenkins_contract.ensure_repo_jenkins_contract(repo_dir, refresh=False)

    jenkinsfile_path.write_text(
        """
pipeline {
    parameters {
        booleanParam(name: 'APPLY', defaultValue: true, description: 'Apply infra')
    }
}
""".strip(),
        encoding="utf-8",
    )

    inspection = repo_jenkins_contract.inspect_repo_jenkins_contract(repo_dir)

    assert inspection.exists is True
    assert inspection.fresh is False
    assert any("stale" in message.lower() for message in inspection.messages)


def test_detect_pipeline_style_marks_ghdp_native_pipeline() -> None:
    style = repo_jenkins_contract._detect_pipeline_style(
        """
stage("Build") {
    steps {
        sh 'ghdp ci setup'
        sh 'ghdp build'
        sh 'ghdp deploy --env dev --yes'
    }
}
"""
    )

    assert style == "ghdp_native_pipeline"


def test_load_repo_jenkins_contract_upgrades_legacy_schema(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    contract_path = repo_dir / ".ghdp" / "ci"
    contract_path.mkdir(parents=True)
    legacy = {
        "schema_version": "1.0",
        "generated_by": "ghdp",
        "generated_at": "2026-04-15T00:00:00+00:00",
        "repo_name": "sample-repo",
        "source": {"branch_name": "feature/demo", "jenkinsfile_path": "Jenkinsfile", "jenkinsfile_hash": "abc"},
        "pipeline": {"style": "repo_local_pipeline", "stage_names": [], "helper_calls": [], "uses_ghdp_commands": False},
        "routing": {"mcp_server": "dpe-jenkins", "feature_to_dev_tool": "create_pull_request", "make_release_tool": "create_release", "develop_build_tool": "deploy"},
        "flows": {
            "feature_to_dev": {"supported": True, "mode": "shared_release_management", "default_branch_prefix": "feature/"},
            "make_release": {"supported": True, "mode": "shared_release_management", "dynamic_params_from_jenkinsfile": False},
            "develop_build": {"supported": False, "mode": "repo_local_pipeline", "default_branch": "develop", "confirm_gate_supported": True},
        },
        "parameter_schema": [],
    }
    (contract_path / "jenkins_contract.json").write_text(json.dumps(legacy), encoding="utf-8")

    payload = repo_jenkins_contract.load_repo_jenkins_contract(repo_dir)

    assert payload["schema_version"] == "2.0"
    assert payload["flows"]["feature_to_dev"]["execution_backend"] == "jenkins_api"
    assert payload["release_surface"]["indexed_choices"][1]["flow"] == "make_release"
