# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.tinker import (
    FileAssist, MindError
)
from mind_app.mcp import McpSessionLike
from mind_app.runtime.mcp.service_runtime import start_service_runtime
from mind_app.stream_events.failure_display import render_failure_text
from mind_core.design import Design
from mind_nova.modes import RunMode
from mind_nova import const
from .repl_tools import render_tools_summary

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


def print_pending_attachments(mind: "Mind") -> None:
    """打印当前待发送附件列表。"""
    attachments = mind.attach.pending_attachments_snapshot()
    if not attachments:
        Design.console.print("[bold #7F8C9A]No pending attachments.[/]")
        print_attach_gap()
        return None

    Design.console.print(f"[bold #AFC7D8]Pending attachments ({len(attachments)}):[/]")
    for index, attachment in enumerate(attachments, start=1):
        size = int(attachment.get("size") or 0)
        Design.console.print(
            f"[bold #AFC7D8]  {index}.[/] "
            f"[bold #F4F7FA]{attachment.get('filename') or '-'}[/] "
            f"[#7F8C9A]({attachment.get('kind') or 'file'} · {size} bytes)[/]"
        )
        Design.console.print(f"[#7F8C9A]     {attachment.get('local') or '-'}[/]")
    print_attach_gap()


def print_attach_gap() -> None:
    Design.console.print()


async def start_helix_runtime(mind: "Mind") -> None:
    """确认本地服务已经启动。"""
    try:
        await start_service_runtime(mind)
    except MindError as error:
        Design.console.print(f"[bold #FF5F5F]Helix start failed: {error}[/]")
        Design.console.print()
        return None


async def open_pref_page() -> None:
    """打开偏好配置页。"""
    url = f"{const.BASE_URL.rstrip('/')}/pref"
    Design.console.print(
        f"[bold #AFC7D8]Preferences[/] [dim #7F8C9A]· {url}[/]"
    )
    try:
        await FileAssist.open_url(url)
    except Exception as open_error:
        Design.console.print(
            f"[bold #FF5F5F]Open preferences failed: "
            f"{type(open_error).__name__}: {open_error}[/]"
        )
    Design.console.print()


async def print_available_tools(
    mind: "Mind",
    *,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any]
) -> None:
    """建立一次 MCP 会话并打印当前模式可见工具。"""
    async def render_tools_with_session(
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
    ) -> None:
        _ = session

        render_tools_summary(
            mode=run_mode,
            tools=tools,
        )

    try:
        await mind.with_mcp_session(pref_config, render_tools_with_session)

    except (KeyboardInterrupt, SystemExit):
        raise

    except BaseException as tool_error:
        message = str(tool_error).strip()
        error   = f"{type(tool_error).__name__}: {message}" if message else type(tool_error).__name__

        Design.console.print(render_failure_text("tools.failed", error))
        Design.console.print()


if __name__ == '__main__':
    pass
