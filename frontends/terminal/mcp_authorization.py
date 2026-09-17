# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import re

from agent.domain.mcp_authorization import McpAuthorizationStatus
from metadata import const
from frontends.terminal.text import sanitize_terminal_line


def authorization_recovery(
    status: McpAuthorizationStatus, name: str, connection: str | None = None,
) -> str | None:
    """从认证事实生成针对原始注册的恢复提示，不把名称解释为 Shell 代码。"""
    label = json.dumps(sanitize_terminal_line(name), ensure_ascii=False)
    restart = f"Select {label} in /mcp and restart it."
    if status.state in ("header", "bearer") and status.verification == "rejected":
        return f"Check the configured {status.state} credentials for {label}. {restart}"
    if status.state in ("not_logged_in", "reauthorization_required"):
        login = (
            f"Run {const.APP_NAME} mcp login {name}"
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
            else f"Use {const.APP_NAME} mcp login with the exact server name {label}"
        )
        return f"{login}. {restart}"
    if status.state == "unavailable":
        return f"Check local credential storage for {label}, then retry status."
    if connection in ("failed", "stopped") and status.state == "oauth":
        return restart
    return None


def authorization_fields(
    status: McpAuthorizationStatus, name: str, connection: str | None = None,
) -> tuple[tuple[str, str], ...]:
    """供 CLI、MCP 菜单与工具摘要共同展示本地凭据及最近认证观察。"""
    fields = [
        ("Auth", status.state),
        ("Credentials (local)", status.credentials or (
            "not_applicable" if status.state in ("unsupported", "header", "bearer") else "unknown"
        )),
        ("Last auth request", status.verification),
    ]
    if status.error is not None:
        fields.append(("Authorization error", status.error))
    hint = authorization_recovery(status, name, connection)
    if hint is not None:
        fields.append(("Recovery", hint))
    return tuple(fields)


if __name__ == '__main__':
    pass
