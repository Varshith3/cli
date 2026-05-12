from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from platform_cli.tools.service import ToolOnboardingStatus


@dataclass(frozen=True)
class InstallCommandIssue:
    phase: str
    outcome: str
    code: str
    short_status: str
    next_action: str = ""
    detail_hint: str = ""
    tool_name: str = ""


def make_issue(
    *,
    phase: str,
    outcome: str,
    code: str,
    short_status: str,
    next_action: str = "",
    detail_hint: str = "",
    tool_name: str = "",
) -> InstallCommandIssue:
    return InstallCommandIssue(
        phase=phase,
        outcome=outcome,
        code=str(code or "E_INSTALL_SESSION").strip() or "E_INSTALL_SESSION",
        short_status=str(short_status or "").strip() or "Install session issue",
        next_action=str(next_action or "").strip(),
        detail_hint=str(detail_hint or "").strip(),
        tool_name=str(tool_name or "").strip(),
    )


def issue_from_exception(
    *,
    phase: str,
    exc: Exception,
    outcome: str,
    short_status: str,
    next_action: str = "",
    tool_name: str = "",
) -> InstallCommandIssue:
    return make_issue(
        phase=phase,
        outcome=outcome,
        code=getattr(exc, "code", "") or exc.__class__.__name__ or "E_INSTALL_SESSION",
        short_status=short_status,
        next_action=next_action,
        detail_hint=str(exc).strip() or exc.__class__.__name__,
        tool_name=tool_name,
    )


def _group_install_results(results: list[ToolOnboardingStatus]) -> dict[str, list[ToolOnboardingStatus]]:
    grouped = {
        "ready": [],
        "action_required": [],
        "failed": [],
        "skipped": [],
    }
    for item in results:
        bucket = "ready" if item.status in {"ready", "already_ready"} else item.status
        if bucket not in grouped:
            bucket = "failed"
        grouped[bucket].append(item)
    return grouped


def _group_install_issues(issues: list[InstallCommandIssue]) -> dict[str, list[InstallCommandIssue]]:
    grouped = {
        "action_required": [],
        "failed": [],
        "warning": [],
    }
    for item in issues:
        bucket = item.outcome if item.outcome in grouped else "failed"
        grouped[bucket].append(item)
    return grouped


def _phase_label(phase: str) -> str:
    cleaned = str(phase or "").strip()
    if not cleaned:
        return "unknown step"
    return cleaned.replace(".", " -> ")


def _render_detailed_install_summary(
    grouped: dict[str, list[ToolOnboardingStatus]],
    issue_grouped: dict[str, list[InstallCommandIssue]],
    *,
    echo: Callable[[str], None],
) -> None:
    sections = [
        ("ready", "ready"),
        ("action_required", "action required"),
        ("failed", "failed"),
        ("skipped", "skipped"),
    ]

    echo("")
    echo("tools install summary:")
    if issue_grouped["failed"] or issue_grouped["action_required"] or issue_grouped["warning"]:
        echo("command issues:")
        for key in ("failed", "action_required", "warning"):
            for item in issue_grouped[key]:
                prefix = f"{item.tool_name} " if item.tool_name else ""
                echo(f"  - {prefix}[{item.phase}] {item.short_status}")
                echo(f"    outcome={item.outcome} code={item.code}")
                if item.next_action:
                    echo(f"    next: {item.next_action}")
                if item.detail_hint:
                    echo(f"    detail: {item.detail_hint}")

    for key, label in sections:
        items = grouped[key]
        if not items:
            continue
        echo(f"{label}:")
        for item in items:
            prefix = f"[{item.phase}] " if item.phase else ""
            echo(f"  - {item.tool_name}: {prefix}{item.short_status}")
            if item.next_action:
                echo(f"    next: {item.next_action}")
            if item.detail_hint:
                echo(f"    detail: {item.detail_hint}")


def _render_compact_install_summary(
    grouped: dict[str, list[ToolOnboardingStatus]],
    issue_grouped: dict[str, list[InstallCommandIssue]],
    *,
    echo: Callable[[str], None],
) -> None:
    sections = [
        ("ready", "ready"),
        ("action_required", "next"),
        ("failed", "failed"),
    ]

    echo("")
    echo("tools install summary:")
    if issue_grouped["failed"]:
        echo("issues:")
        for item in issue_grouped["failed"]:
            label = item.tool_name or "session"
            echo(f"  - {label}: issue in {_phase_label(item.phase)}")
            if item.next_action:
                echo(f"    next: {item.next_action}")
        echo("  rerun with `ghdp tools install --debug-install` for full diagnostics.")
    elif not grouped["action_required"] and not grouped["failed"]:
        echo("no issues.")

    for key, label in sections:
        items = grouped[key]
        if not items:
            continue
        echo(f"{label}:")
        for item in items:
            prefix = f"{_phase_label(item.phase)}: " if item.phase and key == "failed" else ""
            echo(f"  - {item.tool_name}: {prefix}{item.short_status}")
            if item.next_action:
                echo(f"    next: {item.next_action}")


def render_install_summary(
    results: list[ToolOnboardingStatus],
    issues: list[InstallCommandIssue],
    *,
    echo: Callable[[str], None],
    debug: bool = False,
) -> None:
    grouped = _group_install_results(results)
    issue_grouped = _group_install_issues(issues)
    if debug:
        _render_detailed_install_summary(grouped, issue_grouped, echo=echo)
        return
    _render_compact_install_summary(grouped, issue_grouped, echo=echo)


def install_summary_has_failures(results: list[ToolOnboardingStatus], issues: list[InstallCommandIssue]) -> bool:
    return any(item.status == "failed" for item in results) or any(item.outcome == "failed" for item in issues)


def install_summary_has_follow_up(results: list[ToolOnboardingStatus], issues: list[InstallCommandIssue]) -> bool:
    return any(item.status == "action_required" for item in results) or any(
        item.outcome == "action_required" for item in issues
    )
