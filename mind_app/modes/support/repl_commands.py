# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import httpx
import typing
from engine.tinker import FileAssist
from mind_core.design import Design
from mind_nova import const
from mind_nova.modes import RunMode
from mind_app.mcp import McpSessionLike
from mind_app.stream_events.failure_display import render_failure_text
from .repl_prompt import save_primary_pref_field
from .repl_tools import render_tools_summary

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


async def exchange_pref_value(
    matcher: re.Match[str],
    pref_command: typing.Literal["model", "apikey", "base-url"]
) -> typing.Optional[str]:
    """解析模型偏好类指令，并给出交互提示。"""
    if pref_name := matcher.group(1).strip() if matcher.group(1) else None:
        return pref_name

    styles: list[str] = []

    match pref_command:
        case "model":
            styles = ["<model> (Model name or ID)"]
        case "apikey":
            styles = ["<apikey> (Provider API key)"]
        case "base-url":
            styles = ["<url> (Provider base URL)"]

    for style in styles:
        Design.console.print(f"[bold #AFC7D8]  • {style}[/]")
    Design.console.print(
        f"[bold #FF5F5F]\n {pref_command} invalid: /{pref_command} {const.ERR}{pref_name}"
    )
    Design.console.print()
    return None


async def persist_primary_pref(
    mind: "Mind",
    *,
    command_name: typing.Literal["model", "apikey", "base-url"],
    field_name: typing.Literal["model", "apikey", "base_url"],
    field_value: str
) -> typing.Optional[dict[str, typing.Any]]:
    """把 REPL 偏好命令写入 primary slot，并刷新本地缓存。"""
    try:
        saved = await save_primary_pref_field(field_name, field_value)
        await mind.refresh_pref_if_stale(ttl_sec=0.0)
    except (httpx.HTTPError, ValueError) as pref_save_error:
        Design.console.print(
            f"[bold #FF5F5F]{command_name} save failed: "
            f"{type(pref_save_error).__name__}: {pref_save_error}[/]"
        )
        Design.console.print()
        return None

    saved_primary = saved.get("primary") if isinstance(saved, dict) else {}
    return saved_primary if isinstance(saved_primary, dict) else {}


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
