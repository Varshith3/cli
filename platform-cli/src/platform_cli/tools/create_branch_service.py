from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from platform_cli.core.config import get_bool, get_value
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import gh_auth_ready, gh_subprocess_env, is_managed_install
from platform_cli.exec.runner import run_cmd
from platform_cli.state.repo_intent_store import persist_repo_intent
from platform_cli.tools.branch_ai import BranchIntent, build_intent_prompt, choose_provider, generate_intent
from platform_cli.tools.create_branch_policy import CreateBranchPolicy, load_create_branch_policy
from platform_cli.tools.jira_context import JiraValidationResult, comment_on_jira_ticket, fetch_jira_context, validate_jira_ticket
from platform_cli.tools.repo_branch_checkout import CheckoutResult, checkout_remote_branch_if_safe


JIRA_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
VALID_INTENT_MODES = {"auto", "generate", "provided", "skip"}


@dataclass(frozen=True)
class BranchCreateRequest:
    branch: Optional[str] = None
    branch_type: Optional[str] = None
    ticket: Optional[str] = None
    slug: Optional[str] = None
    base_branch: Optional[str] = None
    repo: Optional[str] = None
    validate_jira: Optional[bool] = None
    comment_on_jira: Optional[bool] = None
    intent_mode: str = "auto"
    intent_text: Optional[str] = None
    provider: str = "auto"
    request_id: Optional[str] = None
    local_checkout: bool = True
    persist_intent: bool = True
    commit_intent: bool = False
    dry_run: bool = False
    intent_prompt_file: Optional[str] = None


@dataclass(frozen=True)
class BranchCreateResult:
    repo: str
    ticket: str
    branch_type: str
    slug: str
    base_branch: str
    branch_name: str
    request_id: str
    branch_created: bool
    dry_run: bool
    jira_validated: bool
    jira_warning: str
    jira_comment_posted: bool
    intent_provider: str
    intent_generated: bool
    intent_saved: bool
    intent_committed: bool
    intent_path: str
    local_checkout_message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "ticket": self.ticket,
            "branch_type": self.branch_type,
            "slug": self.slug,
            "base_branch": self.base_branch,
            "branch_name": self.branch_name,
            "request_id": self.request_id,
            "branch_created": self.branch_created,
            "dry_run": self.dry_run,
            "jira_validated": self.jira_validated,
            "jira_warning": self.jira_warning,
            "jira_comment_posted": self.jira_comment_posted,
            "intent_provider": self.intent_provider,
            "intent_generated": self.intent_generated,
            "intent_saved": self.intent_saved,
            "intent_committed": self.intent_committed,
            "intent_path": self.intent_path,
            "local_checkout_message": self.local_checkout_message,
        }


def create_branch(request: BranchCreateRequest) -> BranchCreateResult:
    policy = load_create_branch_policy()
    _ensure_gh_available()
    _ensure_gh_authenticated()

    resolved = _resolve_branch_inputs(
        policy=policy,
        branch=request.branch,
        branch_type=request.branch_type,
        ticket=request.ticket,
        slug=request.slug,
    )
    target_repo = _resolve_repo(request.repo)
    base_branch = (request.base_branch or "").strip() or _resolve_default_branch(target_repo)
    branch_name = _build_branch_name(policy, resolved["ticket"], resolved["branch_type"], resolved["slug"])
    request_id = _resolve_request_id(request.request_id)

    jira_mode = _resolve_jira_mode(request.validate_jira)
    jira_validation = validate_jira_ticket(resolved["ticket"], mode=jira_mode)
    jira_ctx = fetch_jira_context(resolved["ticket"], mode=jira_mode)
    intent_prompt = build_intent_prompt(
        jira_title=jira_ctx.get("summary", ""),
        jira_description=_intent_description(jira_ctx.get("description", "")),
        branch_name=branch_name,
        branch_type=resolved["branch_type"],
        branch_slug=resolved["slug"],
        ticket_key=resolved["ticket"],
        repo=target_repo,
        base_branch=base_branch,
    )
    _write_intent_prompt_file(request.intent_prompt_file, intent_prompt)

    branch_created = False
    if not request.dry_run:
        _create_remote_branch(repo=target_repo, base_branch=base_branch, branch_name=branch_name)
        branch_created = True

    intent = _resolve_branch_intent(
        request=request,
        target_repo=target_repo,
        base_branch=base_branch,
        branch_name=branch_name,
        branch_type=resolved["branch_type"],
        branch_slug=resolved["slug"],
        ticket_key=resolved["ticket"],
        jira_summary=jira_ctx.get("summary", ""),
        jira_description=jira_ctx.get("description", ""),
    )

    checkout = CheckoutResult(
        False,
        "Intent persistence was skipped.",
        repo_root=None,
    )
    intent_saved = False
    intent_committed = False
    intent_path = ""

    if request.local_checkout and not request.dry_run:
        checkout = checkout_remote_branch_if_safe(repo=target_repo, branch_name=branch_name)
    elif not request.local_checkout:
        checkout = CheckoutResult(False, "Local checkout was disabled by flag.", repo_root=None)
    elif request.dry_run:
        checkout = CheckoutResult(False, "Dry-run mode skipped local checkout.", repo_root=None)

    if request.commit_intent and (request.dry_run or not request.local_checkout):
        raise PlatformError(
            "Committing the repo intent requires a non-dry-run execution with local checkout enabled.",
            code="E_BRANCH_INTENT_COMMIT_REQUIRES_CHECKOUT",
            reason="intent_commit",
        )

    if request.persist_intent and intent is not None and checkout.persist_ready:
        path = persist_repo_intent(
            repo_root=checkout.repo_root,
            intent=intent.intent,
            summary=jira_ctx.get("description") or jira_ctx.get("summary") or "",
            provider=intent.provider,
            relative_path=policy.intent_repo_path,
            branch_name=branch_name,
            ticket_key=resolved["ticket"],
            source="branch_create_generated",
        )
        intent_saved = True
        intent_path = str(path)
        if request.commit_intent:
            intent_committed = _commit_intent_file(
                repo_root=checkout.repo_root,
                branch_name=branch_name,
                intent_path=path,
            )
    elif request.commit_intent:
        raise PlatformError(
            "Repo-local intent commit was requested, but the branch could not be checked out safely.",
            code="E_BRANCH_INTENT_COMMIT_NOT_READY",
            reason="intent_commit",
        )

    jira_comment_posted = False
    if bool(request.comment_on_jira):
        if request.dry_run:
            raise PlatformError(
                "Dry-run mode cannot comment on Jira because no branch was created yet.",
                code="E_BRANCH_DRY_RUN_COMMENT_UNSUPPORTED",
                reason="jira_comment",
            )
        comment_on_jira_ticket(
            resolved["ticket"],
            _render_jira_comment(
                policy=policy,
                repo=target_repo,
                branch_name=branch_name,
                request_id=request_id,
            ),
        )
        jira_comment_posted = True

    return BranchCreateResult(
        repo=target_repo,
        ticket=resolved["ticket"],
        branch_type=resolved["branch_type"],
        slug=resolved["slug"],
        base_branch=base_branch,
        branch_name=branch_name,
        request_id=request_id,
        branch_created=branch_created,
        dry_run=request.dry_run,
        jira_validated=jira_validation.found,
        jira_warning=jira_validation.warning,
        jira_comment_posted=jira_comment_posted,
        intent_provider=intent.provider if intent else "",
        intent_generated=bool(intent and intent.provider not in {"manual", "provided"}),
        intent_saved=intent_saved,
        intent_committed=intent_committed,
        intent_path=intent_path,
        local_checkout_message=checkout.message,
    )


def _resolve_branch_inputs(
    *,
    policy: CreateBranchPolicy,
    branch: Optional[str],
    branch_type: Optional[str],
    ticket: Optional[str],
    slug: Optional[str],
) -> dict[str, str]:
    shorthand = _parse_branch_shorthand(policy, branch)
    final_ticket = (ticket or shorthand.get("ticket") or "").strip().upper()
    final_type_raw = (branch_type or shorthand.get("branch_type") or "").strip()
    final_slug_raw = (slug or shorthand.get("slug") or "").strip()

    if not final_ticket:
        raise PlatformError(
            "Ticket key missing. Pass --ticket or include it in the shorthand branch name.",
            code="E_TICKET_MISSING",
            reason="ticket",
        )
    if not JIRA_RE.match(final_ticket):
        raise PlatformError(
            f"Invalid ticket format '{final_ticket}'. Expected something like EPPE-6654.",
            code="E_TICKET_INVALID",
            reason="ticket",
        )

    if not final_type_raw:
        raise PlatformError(
            "Branch type missing. Pass --type or include it in the shorthand branch name.",
            code="E_BRANCH_TYPE_MISSING",
            reason="branch_type",
        )

    if not final_slug_raw:
        raise PlatformError(
            "Branch slug missing. Pass --slug or include it in the shorthand branch name.",
            code="E_SLUG_MISSING",
            reason="slug",
        )

    return {
        "ticket": final_ticket,
        "branch_type": _normalize_type(policy, final_type_raw),
        "slug": _sanitize_slug(final_slug_raw),
    }


def _parse_branch_shorthand(policy: CreateBranchPolicy, branch: Optional[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    if not branch:
        return result

    value = branch.strip()
    if "/" in value:
        value = value.split("/", 1)[1].strip()

    match = re.match(r"^([A-Za-z][A-Za-z0-9]+-\d+)-([A-Za-z]+)-(.+)$", value)
    if not match:
        raise PlatformError(
            "Invalid shorthand branch format. Expected feature/EPPE-6654-ENHANCEMENT-branch-orchestration "
            "or EPPE-6654-ENHANCEMENT-branch-orchestration.",
            code="E_BRANCH_FORMAT_INVALID",
            reason="branch",
        )

    result["ticket"] = match.group(1).upper()
    result["branch_type"] = _normalize_type(policy, match.group(2))
    result["slug"] = _sanitize_slug(match.group(3))
    return result


def _normalize_type(policy: CreateBranchPolicy, value: str) -> str:
    normalized = policy.type_aliases.get((value or "").strip().lower(), (value or "").strip().upper())
    if normalized not in policy.allowed_types:
        raise PlatformError(
            f"Invalid branch type '{value}'. Allowed values: {', '.join(sorted(policy.allowed_types))}",
            code="E_BRANCH_TYPE_INVALID",
            reason="branch_type",
        )
    return normalized


def _sanitize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    if not slug:
        raise PlatformError(
            "Branch slug is required and must contain letters or numbers.",
            code="E_SLUG_INVALID",
            reason="slug",
        )
    return slug


def _resolve_repo(repo: Optional[str]) -> str:
    if repo:
        return repo.strip()

    res = _gh_run_cmd(["gh", "repo", "view", "--json", "nameWithOwner"], check=False)
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout or "{}")
            value = str(data.get("nameWithOwner") or "").strip()
            if value:
                return value
        except Exception:
            pass

    if cli_ctx.non_interactive:
        raise PlatformError(
            "Repo could not be inferred. Run from inside the target repo or pass --repo OWNER/REPO.",
            code="E_REPO_NOT_DETECTED",
            reason="repo",
        )
    raise PlatformError(
        "Repo could not be inferred. Run from inside the target repo or pass --repo OWNER/REPO.",
        code="E_REPO_NOT_DETECTED",
        reason="repo",
    )


def _resolve_default_branch(repo: str) -> str:
    res = _gh_run_cmd(["gh", "repo", "view", repo, "--json", "defaultBranchRef"], check=True)
    try:
        data = json.loads(res.stdout or "{}")
        branch = str(((data.get("defaultBranchRef") or {}) if isinstance(data, dict) else {}).get("name") or "").strip()
    except Exception:
        branch = ""
    if not branch:
        raise PlatformError(
            f"Could not resolve the default branch for repo '{repo}'.",
            code="E_DEFAULT_BRANCH_NOT_FOUND",
            reason="repo",
        )
    return branch


def _build_branch_name(policy: CreateBranchPolicy, ticket: str, branch_type: str, slug: str) -> str:
    return f"{policy.branch_prefix}/{ticket}-{branch_type}-{slug}"


def _create_remote_branch(*, repo: str, base_branch: str, branch_name: str) -> None:
    sha_res = _gh_run_cmd(
        ["gh", "api", f"repos/{repo}/git/ref/heads/{base_branch}", "--jq", ".object.sha"],
        check=True,
    )
    sha = (sha_res.stdout or "").strip()
    if not sha:
        raise PlatformError(
            f"Unable to resolve the SHA for base branch '{base_branch}'.",
            code="E_BASE_SHA_NOT_FOUND",
            reason="base_branch",
        )

    _gh_run_cmd(
        [
            "gh",
            "api",
            f"repos/{repo}/git/refs",
            "--method",
            "POST",
            "-f",
            f"ref=refs/heads/{branch_name}",
            "-f",
            f"sha={sha}",
        ],
        check=True,
    )


def _resolve_jira_mode(validate_jira: Optional[bool]) -> str:
    if validate_jira is True:
        return "enforce"
    if validate_jira is False:
        return "skip"
    jira_mode = str(get_value("branch.create.jira_check_mode", "warn") or "warn").strip().lower()
    return jira_mode if jira_mode in {"warn", "enforce"} else "warn"


def _resolve_branch_intent(
    *,
    request: BranchCreateRequest,
    target_repo: str,
    base_branch: str,
    branch_name: str,
    branch_type: str,
    branch_slug: str,
    ticket_key: str,
    jira_summary: str,
    jira_description: str,
) -> BranchIntent | None:
    mode = _resolve_intent_mode(request.intent_mode, request.intent_text)
    if mode == "skip":
        return None

    if mode == "provided":
        text = (request.intent_text or "").strip()
        if not text:
            raise PlatformError(
                "Intent text is required when --intent-mode provided is used.",
                code="E_BRANCH_INTENT_TEXT_MISSING",
                reason="intent",
            )
        return BranchIntent(intent=text, provider="provided", generated_at=_now_iso())

    provider_pref = (request.provider or "auto").strip().lower()
    if provider_pref == "auto":
        provider_pref = str(get_value("branch.ai.provider", "auto") or "auto").strip().lower()
    provider = choose_provider(
        preferred=provider_pref,
        refresh_on_missing=get_bool("branch.ai.refresh_on_missing", True),
    )

    description = _intent_description(jira_description)
    if provider == "manual":
        raise PlatformError(
            "Manual intent entry must be resolved by the CLI command layer. Pass --intent-text/--intent-file or choose an AI provider.",
            code="E_BRANCH_INTENT_MANUAL_REQUIRED",
            reason="intent",
        )

    generated = generate_intent(
        provider=provider,
        jira_summary=jira_summary,
        jira_description=description,
        branch_name=branch_name,
        branch_type=branch_type,
        branch_slug=branch_slug,
        ticket_key=ticket_key,
        repo=target_repo,
        base_branch=base_branch,
    )

    return generated


def _resolve_intent_mode(intent_mode: str, intent_text: Optional[str]) -> str:
    mode = (intent_mode or "auto").strip().lower()
    if mode not in VALID_INTENT_MODES:
        raise PlatformError(
            f"Invalid intent mode '{intent_mode}'. Use one of: auto, generate, provided, skip.",
            code="E_BRANCH_INTENT_MODE_INVALID",
            reason="intent_mode",
        )
    if mode == "auto":
        if intent_text and intent_text.strip():
            return "provided"
        return "generate" if get_bool("branch.intent.enabled", True) else "skip"
    return mode


def _intent_description(jira_description: str) -> str:
    include_description = get_bool("branch.intent.include_description", True)
    return jira_description if include_description else ""


def _write_intent_prompt_file(path_value: Optional[str], prompt: str) -> None:
    path_text = (path_value or "").strip()
    if not path_text:
        return
    path = Path(path_text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding="utf-8")


def _render_jira_comment(*, policy: CreateBranchPolicy, repo: str, branch_name: str, request_id: str) -> str:
    return policy.jira_comment_template.format(
        branch_name=branch_name,
        actor=_resolve_actor(),
        branch_url=f"https://github.com/{repo}/tree/{branch_name}",
        request_id=request_id,
    )


def _resolve_actor() -> str:
    env_actor = str(os.environ.get("GITHUB_ACTOR", "") or "").strip()
    if env_actor:
        return env_actor
    res = _gh_run_cmd(["gh", "api", "user", "--jq", ".login"], check=False)
    return (res.stdout or "").strip() or "unknown"


def _commit_intent_file(*, repo_root: Path, branch_name: str, intent_path: Path) -> bool:
    rel_path = str(intent_path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    status_before = run_cmd(["git", "status", "--porcelain", "--", rel_path], check=False, capture=True, cwd=repo_root)
    if not (status_before.stdout or "").strip():
        return False

    run_cmd(["git", "add", "--", rel_path], check=True, capture=True, cwd=repo_root)
    status_after = run_cmd(["git", "status", "--porcelain", "--", rel_path], check=False, capture=True, cwd=repo_root)
    if not (status_after.stdout or "").strip():
        return False

    run_cmd(
        ["git", "commit", "-m", f"ghdp: persist branch intent for {branch_name}"],
        check=True,
        capture=True,
        cwd=repo_root,
    )
    run_cmd(["git", "push", "origin", f"HEAD:{branch_name}"], check=True, capture=True, cwd=repo_root)
    return True


def _ensure_gh_available() -> None:
    try:
        run_cmd(["gh", "--version"], check=True)
    except PlatformError as e:
        raise PlatformError(
            "GitHub CLI (gh) is required but not available. Install it via: ghdp tools install --team platform --tool gh",
            code=e.code,
            reason="gh",
        )


def _ensure_gh_authenticated() -> None:
    if gh_auth_ready():
        return
    if is_managed_install():
        raise PlatformError(
            "Managed GitHub auth is not configured for this installation. Reinstall or refresh the managed bundle.",
            code="E_GH_NOT_AUTHENTICATED",
            reason="managed_github_auth_missing",
        )
    raise PlatformError(
        "GitHub CLI is not authenticated. Run: gh auth login",
        code="E_GH_NOT_AUTHENTICATED",
        reason="gh_auth",
    )


def _gh_run_cmd(cmd: list[str], *, check: bool = True, cwd: str | Path | None = None) -> object:
    return run_cmd(cmd, check=check, cwd=cwd, env=gh_subprocess_env())


def _resolve_request_id(value: Optional[str]) -> str:
    explicit = (value or "").strip()
    if explicit:
        return explicit

    run_id = str(os.environ.get("GITHUB_RUN_ID", "") or "").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if run_id:
        return f"{stamp}-{run_id}"
    return stamp


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
