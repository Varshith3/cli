# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import json
import re
import importlib.resources as pkg_resources
import textwrap
from typing import Sequence

_DEFAULT_SUMMARY_SECTIONS = ("summary",)


def _normalize_heading_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().rstrip(":")).lower()


def _load_default_summary_sections() -> tuple[str, ...]:
    try:
        raw = (pkg_resources.files("platform_cli.resources") / "release-summary-defaults.json").read_text(
            encoding="utf-8"
        )
        payload = json.loads(raw)
    except Exception:
        return _DEFAULT_SUMMARY_SECTIONS

    sections = payload.get("summary_sections") if isinstance(payload, dict) else None
    if not isinstance(sections, list):
        return _DEFAULT_SUMMARY_SECTIONS

    normalized: list[str] = []
    for item in sections:
        if not isinstance(item, str):
            continue
        heading = _normalize_heading_name(item)
        if heading:
            normalized.append(heading)
    return tuple(normalized) if normalized else _DEFAULT_SUMMARY_SECTIONS


def extract_most_important_release_note(body: str) -> str:
    text = textwrap.dedent(body or "").strip()
    if not text:
        return "No release notes provided."

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("-", "*", "+")):
            msg = re.sub(r"^[\-\*\+]\s*", "", line).strip()
            if msg:
                return msg

    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        lines.append(line)

    if lines:
        prose = " ".join(lines)
        chunks = [c.strip() for c in re.split(r"[.!?]\s+", prose) if c.strip()]
        if chunks:
            return chunks[0].rstrip(".!?")

    return "No additional notes available."


def extract_release_summary(body: str, section_names: Sequence[str] | None = None) -> str:
    text = textwrap.dedent(body or "").strip()
    if not text:
        return "No release notes provided."

    target_names = (
        tuple(_normalize_heading_name(name) for name in section_names if _normalize_heading_name(name))
        if section_names
        else _load_default_summary_sections()
    )
    if not target_names:
        target_names = _DEFAULT_SUMMARY_SECTIONS

    lines = text.splitlines()
    start_idx = -1
    for idx, raw in enumerate(lines):
        heading = raw.strip()
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", heading)
        if not m:
            continue
        if _normalize_heading_name(m.group(1)) in target_names:
            start_idx = idx + 1
            break

    if start_idx >= 0:
        summary_lines: list[str] = []
        for raw in lines[start_idx:]:
            line = raw.strip()
            if re.match(r"^#{1,6}\s+\S+", line.strip()):
                break
            summary_lines.append(line)

        summary = "\n".join(summary_lines).strip()
        if summary:
            return summary

    return extract_most_important_release_note(text)
