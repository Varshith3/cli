# Re-export from tools layer for backward compatibility.
# All logic moved to platform_cli.tools.terraform.tf_common per ARCHITECTURE.md.
from platform_cli.tools.terraform.tf_common import *  # noqa: F401,F403
from platform_cli.tools.terraform.tf_common import (  # explicit re-exports
    TerraformRuntime,
    build_plan_vars,
    build_runtime,
    build_runtime_without_auth,
    confirm_or_fail,
    ensure_env_allowed,
    load_runtime_policy,
    resolve_planfile,
    run_init_sequence,
    run_validate,
    terraform_workspace_select,
    top_plan_resources,
)
