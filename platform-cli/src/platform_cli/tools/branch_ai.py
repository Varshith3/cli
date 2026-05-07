# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.tools.ai_provider import detect_provider_statuses, generate_text, select_provider


INTENT_PROMPT_TEMPLATE = """You are an engineering intent generator for software development workflows.

Your job is to produce a concise, implementation-oriented intent from a Jira work item.

Generation rules:
- Return only the intent text.
- Write exactly 2 or 3 sentences in a single paragraph.
- Capture only one primary implementation intent.
- The intent must be useful as input for code generation, unit test generation, and developer confirmation.
- Prefer the expected behavior or system outcome over process or planning language.
- Use the Jira title as the strongest signal.
- Use the description as supporting context, but do not trust it blindly if it is noisy, incomplete, outdated, or contradictory.
- Use the branch name as a weak hint.
- Use the branch type and branch slug as user-confirmed framing hints, but keep Jira title and description as the primary signals.
- If details are limited, generate the safest plausible intent that still gives meaningful implementation direction.
- Be specific, but do not invent unsupported requirements.
- Do not include alternatives, caveats, uncertainty, explanations, headings, bullets, markdown, JSON, or labels.
- Do not mention Jira, the ticket structure, branch naming, or missing information.

Input:
Jira title: {jira_title}
Jira description:
{jira_description}
Branch name: {branch_name}
Branch type: {branch_type}
Branch slug: {branch_slug}
Ticket key: {ticket_key}
Repo: {repo}
Base branch: {base_branch}

Generate the intent now.
"""


@dataclass(frozen=True)
class BranchIntent:
    intent: str
    provider: str
    generated_at: str


def choose_provider(*, preferred: str, refresh_on_missing: bool) -> str:
    selected, _ = select_provider(
        preferred=preferred,
        interactive=not bool(cli_ctx.non_interactive),
        refresh_on_missing=refresh_on_missing,
    )
    return selected


def generate_intent(
    *,
    provider: str,
    jira_summary: str,
    jira_description: str,
    branch_name: str,
    branch_type: str,
    branch_slug: str,
    ticket_key: str,
    repo: str,
    base_branch: str,
) -> BranchIntent:
    prompt = build_intent_prompt(
        jira_title=jira_summary,
        jira_description=jira_description,
        branch_name=branch_name,
        branch_type=branch_type,
        branch_slug=branch_slug,
        ticket_key=ticket_key,
        repo=repo,
        base_branch=base_branch,
    )

    if provider not in {"codex", "claude"}:
        raise PlatformError(
            f"Unsupported provider '{provider}'.",
            code="E_BRANCH_AI_PROVIDER_INVALID",
            reason="provider",
        )

    statuses = detect_provider_statuses(refresh=True)
    text = generate_text(provider=provider, statuses=statuses, prompt=prompt).strip()
    if not text:
        raise PlatformError(
            f"{provider} returned an empty intent.",
            code="E_BRANCH_AI_EMPTY",
            reason=provider,
        )

    return BranchIntent(intent=text, provider=provider, generated_at=_now_iso())


def manual_intent(*, jira_summary: str, jira_description: str) -> BranchIntent:
    raise PlatformError(
        "Manual intent entry must be handled by the CLI command layer.",
        code="E_BRANCH_INTENT_MANUAL_REQUIRED",
        reason="intent",
    )


def build_intent_prompt(
    *,
    jira_title: str,
    jira_description: str,
    branch_name: str,
    branch_type: str,
    branch_slug: str,
    ticket_key: str,
    repo: str,
    base_branch: str,
) -> str:
    return INTENT_PROMPT_TEMPLATE.format(
        jira_title=(jira_title or "").strip(),
        jira_description=(jira_description or "").strip() or "(none provided)",
        branch_name=branch_name.strip(),
        branch_type=branch_type.strip(),
        branch_slug=branch_slug.strip(),
        ticket_key=ticket_key.strip(),
        repo=repo.strip(),
        base_branch=base_branch.strip(),
    )
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
