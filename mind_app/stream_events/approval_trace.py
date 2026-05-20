# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .tool_trace import PREVIEW_STYLE, TITLE_STYLE, ERROR_STYLE

APPROVAL_STYLE = "bold #F2C94C"


def command_text(command: typing.Any) -> str:
    if isinstance(command, list):
        return " ".join(str(item) for item in command)
    return str(command or "").strip()


def approval_summary(approval: dict[str, typing.Any]) -> str:
    command = command_text(approval.get("command"))
    tool = str(approval.get("tool") or "").strip()
    return command or tool or "tool call"


def approval_preview_lines(approval: dict[str, typing.Any]) -> list[str]:
    lines: list[str] = []
    cwd = str(approval.get("cwd") or "").strip()
    reason = str(approval.get("reason") or "").strip()
    risk = str(approval.get("risk") or "").strip()
    category = str(approval.get("category") or "").strip()

    if cwd:
        lines.append(f"cwd={cwd}")
    if reason:
        lines.append(f"reason={reason}")
    if risk or category:
        detail = " ".join(part for part in [f"risk={risk}" if risk else "", f"category={category}" if category else ""] if part)
        lines.append(detail)
    return lines


def render_approval_pending_trace(approval: dict[str, typing.Any]) -> str:
    summary = approval_summary(approval)
    return f"• Approval required {summary}".rstrip()


def render_approval_approved_trace(approval: dict[str, typing.Any]) -> str:
    summary = approval_summary(approval)
    return f"✔ You approved mind to run {summary} this time".rstrip()


def render_approval_denied_trace(approval: dict[str, typing.Any]) -> str:
    summary = approval_summary(approval)
    return f"• You denied mind to run {summary}".rstrip()


def render_approval_trace_text(
    title: str,
    *,
    approval: dict[str, typing.Any] | None = None,
    include_preview: bool = True
) -> str:
    lines = approval_preview_lines(approval or {}) if include_preview else []
    if not lines:
        return title
    preview = "\n  ".join(lines)
    return f"{title}\n└ {preview}"


def render_approval_trace_parts(
    title: str,
    *,
    approval: dict[str, typing.Any] | None = None,
    state: typing.Literal["pending", "approved", "denied"] = "pending"
) -> list[dict[str, typing.Optional[str]]]:
    if state == "denied":
        title_style = ERROR_STYLE
    elif state == "approved":
        title_style = TITLE_STYLE
    else:
        title_style = APPROVAL_STYLE

    parts: list[dict[str, typing.Optional[str]]] = [
        {"text": title, "style": title_style}
    ]

    lines = approval_preview_lines(approval or {}) if state == "pending" else []
    if lines:
        parts.extend([
            {"text": "\n", "style": None},
            {"text": "└ ", "style": PREVIEW_STYLE},
            {"text": "\n  ".join(lines), "style": PREVIEW_STYLE},
        ])
    return parts


if __name__ == '__main__':
    pass
