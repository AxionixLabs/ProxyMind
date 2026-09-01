# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections import defaultdict
from prompt_toolkit.utils import get_cwidth
from metadata import const
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView
)
from agent.ports import McpSessionPort
from agent.ports.presentation import (
    TextSpan,
    TextStyle
)
from ..core.styles import (
    FAILURE_STYLE,
    fragment_block
)

GROUP_DISPLAY_LIMIT    = 64
DEFAULT_TERMINAL_WIDTH = 120
TOOLS_COMMAND_STYLE    = TextStyle(foreground="ansimagenta")
TOOLS_HEADING_STYLE    = TextStyle(bold=True)
TOOLS_SECONDARY_STYLE  = TextStyle(dim=True)
TOOLS_TEXT_STYLE       = TextStyle()
TOOLS_EMPTY_STYLE      = TextStyle(italic=True)
BUILTIN_TOOL_LABEL     = f"{const.APP_DESC} Native"

if typing.TYPE_CHECKING:
    from ...controller import Mind


def _terminal_width(
    application: ApplicationSink,
    terminal_width: int | None
) -> int:
    """返回工具摘要使用的有效终端宽度。"""
    if isinstance(terminal_width, int) and terminal_width > 0:
        return terminal_width

    viewport = getattr(application, "viewport", None)
    width    = getattr(viewport, "width", None)

    if isinstance(width, int) and width > 0:
        return width

    return DEFAULT_TERMINAL_WIDTH


def _tool_name_lines(
    names: list[str],
    *,
    terminal_width: int,
    limit: int
) -> list[str]:
    """把工具名按终端宽度转换为带悬挂缩进的文本行。"""
    visible_names = names[:max(0, int(limit))]
    if not visible_names:
        return ["    • Tools: (none)"]

    first_prefix: str        = "    • Tools: "
    continuation_prefix: str = "      "
    lines: list[str]         = []
    current_prefix: str      = first_prefix
    current_names: list[str] = []

    for index, name in enumerate(visible_names):

        suffix    = "," if index < len(visible_names) - 1 else ""
        token     = f"{name}{suffix}"
        candidate = f"{current_prefix}{' '.join(current_names + [token])}"

        if current_names and get_cwidth(candidate) > terminal_width:
            lines.append(f"{current_prefix}{' '.join(current_names)}")
            current_prefix = continuation_prefix
            current_names = []

        current_names.append(token)

    if current_names:
        lines.append(f"{current_prefix}{' '.join(current_names)}")

    remaining = len(names) - len(visible_names)
    if remaining > 0:
        lines.append(f"{continuation_prefix}... and {remaining} more")

    return lines


def _tools_for_display(
    session: McpSessionPort,
    tools: list[dict[str, typing.Any]]
) -> list[dict[str, typing.Any]]:
    """复制工具目录，并把外接工具限定名替换为服务原始名称。"""
    external_group = getattr(session, "external_group", None)
    source_tools   = getattr(external_group, "tools", {})

    original_names = {
        str(qualified_name): str(getattr(tool, "name", "") or "").strip()
        for qualified_name, tool in dict(source_tools or {}).items()
    }

    display_tools: list[dict[str, typing.Any]] = []

    for tool in tools:
        name = str(tool.get("name") or "")

        original_name = original_names.get(name, "")
        if not original_name:
            display_tools.append(tool)
            continue

        display_tool = dict(tool)
        display_tool["name"] = original_name
        display_tools.append(display_tool)

    return display_tools


def summarize_tool_groups(
    tools: list[dict[str, typing.Any]]
) -> list[dict[str, typing.Any]]:
    """按工具来源和提供者汇总工具列表。"""
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    auth_by_group: dict[tuple[str, str, str], str] = {}

    for tool in tools:
        name = str(tool.get("name") or "").strip() if isinstance(tool, dict) else ""
        if not name:
            continue

        meta = tool.get("meta") if isinstance(tool.get("meta"), dict) else {}
        if bool(meta.get("external")):
            label     = str(meta.get("server") or "external").strip() or "external"
            transport = str(meta.get("transport") or "external").strip() or "external"
            auth      = str(meta.get("auth") or "Unsupported").strip() or "Unsupported"
            key       = ("external", label, transport)

            auth_by_group.setdefault(key, auth)

        elif bool(meta.get("client_builtin")):
            key = ("builtin", BUILTIN_TOOL_LABEL, "in-process")
            auth_by_group[key] = "N/A"

        else:
            key = ("builtin", "Helix MCP", "local")
            auth_by_group[key] = "Managed"

        grouped[key].append(name)

    result: list[dict[str, typing.Any]] = []

    for (source, label, detail), names in grouped.items():
        result.append({
            "source": source,
            "label": label,
            "detail": detail,
            "auth": auth_by_group.get((source, label, detail), "Unknown"),
            "tools": sorted(names)
        })

    return sorted(
        result,
        key=lambda item: (
            0 if item["source"] == "builtin" else 1,
            0 if item["label"] == BUILTIN_TOOL_LABEL else 1,
            str(item["label"]),
            str(item["detail"])
        )
    )


def render_tools_summary(
    *,
    application: ApplicationSink,
    tools: list[dict[str, typing.Any]],
    limit: int = GROUP_DISPLAY_LIMIT,
    terminal_width: int | None = None
) -> None:
    """打印当前会话可见工具摘要。"""
    groups = summarize_tool_groups(tools)
    width  = _terminal_width(application, terminal_width)

    parts = [
        TextSpan("/tools", TOOLS_COMMAND_STYLE),
        TextSpan("\n\n", TOOLS_TEXT_STYLE),
        TextSpan("🔌  Tools", TOOLS_HEADING_STYLE),
        TextSpan("\n\n", TOOLS_TEXT_STYLE),
    ]

    if not groups:
        parts.append(TextSpan(
            "  • No tools available.",
            TOOLS_EMPTY_STYLE,
        ))
    else:
        for index, group in enumerate(groups):
            if index:
                parts.append(TextSpan("\n\n", TOOLS_TEXT_STYLE))

            names  = group["tools"]
            label  = group["label"]
            detail = group["detail"]
            auth   = group["auth"]

            parts.extend([
                TextSpan("  • ", TOOLS_TEXT_STYLE),
                TextSpan(label, TOOLS_TEXT_STYLE),
                TextSpan("\n    • Auth: ", TOOLS_TEXT_STYLE),
                TextSpan(auth, TOOLS_TEXT_STYLE),
                TextSpan("\n    • Transport: ", TOOLS_TEXT_STYLE),
                TextSpan(detail, TOOLS_TEXT_STYLE),
            ])

            for line in _tool_name_lines(
                names,
                terminal_width=width,
                limit=limit,
            ):
                parts.extend([
                    TextSpan("\n", TOOLS_TEXT_STYLE),
                    TextSpan(line, TOOLS_TEXT_STYLE),
                ])

    application.emit(ApplicationView(
        type="tui.tools.summary",
        renderable=fragment_block(*parts),
    ))
    application.emit(ApplicationView(type="tui.gap"))
    return None


async def print_available_tools(
    mind: "Mind",
    *,
    pref_config: dict[str, typing.Any]
) -> None:
    """建立一次 MCP 会话并打印当前模式可见工具。"""
    async def render_tools_with_session(
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]]
    ) -> None:
        render_tools_summary(
            application=mind.frontend.application,
            tools=_tools_for_display(session, tools),
            terminal_width=mind.frontend.application.viewport.width,
        )

    try:
        await mind.execution.with_mcp_session(
            pref_config,
            render_tools_with_session,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as tool_error:
        message = str(tool_error).strip()

        error = (
            f"{type(tool_error).__name__}: {message}"
            if message
            else type(tool_error).__name__
        )

        application = mind.frontend.application

        application.emit(ApplicationView(
            type="tui.command",
            renderable=fragment_block(
                TextSpan("/tools", TOOLS_COMMAND_STYLE),
                TextSpan(" · ", TOOLS_SECONDARY_STYLE),
                TextSpan(f"Failed: {error}", FAILURE_STYLE),
            ),
        ))

        application.emit(ApplicationView(type="tui.gap"))


if __name__ == '__main__':
    pass
