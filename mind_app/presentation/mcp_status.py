# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from .styles import (
    ERROR_DOT_STYLE,
    ERROR_PREVIEW_MESSAGE_STYLE,
    PREVIEW_MORE_STYLE,
    SUCCESS_DOT_STYLE
)
from .terminal_text import sanitize_terminal_text
from .text_layout import layout_styled_line

McpStatusLevel = typing.Literal["running", "ready", "warning", "failed"]


@dataclass(frozen=True, slots=True)
class McpStatusDetail(object):
    """描述 MCP 状态中的一条补充信息。"""

    text: str
    state: str = ""


@dataclass(frozen=True, slots=True)
class McpStatusView(object):
    """描述 MCP 快照对应的中立展示状态。"""

    summary: str
    level: McpStatusLevel
    done: bool
    details: tuple[McpStatusDetail, ...] = ()


def inbuild_status_view(snapshot: dict[str, typing.Any]) -> McpStatusView:
    """把内置 MCP 启动快照转换为中立展示状态。"""
    state = str(snapshot.get("state") or "starting").strip().lower()
    title = str(snapshot.get("label") or "Internal MCP").strip() or "Internal MCP"

    if state == "ready":
        return McpStatusView(f"{title} ready", "ready", True)
    if state == "failed":
        detail = str(snapshot.get("error") or snapshot.get("detail") or "").strip()
        details = (
            (McpStatusDetail(f"  └ {detail}", "failed"),)
            if detail
            else ()
        )
        return McpStatusView(f"{title} failed", "failed", True, details)
    return McpStatusView(f"{title} starting", "running", False)


def external_mcp_status_view(
    snapshot: dict[str, typing.Any],
    *,
    detail_limit: int = 5,
) -> McpStatusView:
    """把外部 MCP 启动快照转换为中立展示状态。"""
    summary_override = str(snapshot.get("summary") or "").strip()
    items = tuple(
        item for item in list(snapshot.get("items") or [])
        if isinstance(item, dict)
    )
    done = bool(snapshot.get("done", False))

    if not items:
        detail = str(
            snapshot.get("detail")
            or snapshot.get("stage")
            or snapshot.get("phase")
            or ""
        ).strip()
        summary = (
            f"{summary_override} · {detail}"
            if summary_override and detail
            else summary_override
        )
        return McpStatusView(
            summary=summary,
            level="ready" if done else "running",
            done=done,
        )

    ready_count = 0
    connected_count = 0
    failed_count = 0
    total_tools = 0
    total_filtered = 0

    for item in items:
        state = str(item.get("state") or "").strip().lower()
        if state in {"ready", "empty"}:
            connected_count += 1
            try:
                total_filtered += max(0, int(item.get("filtered") or 0))
            except (TypeError, ValueError, OverflowError):
                pass
        elif state == "failed":
            failed_count += 1

        if state != "ready":
            continue
        try:
            tool_count = int(item.get("tools") or 0)
        except (TypeError, ValueError, OverflowError):
            tool_count = 0
        if tool_count > 0:
            ready_count += 1
            total_tools += tool_count

    if not done:
        prefix = "External MCP linking"
        level = "running"
    elif failed_count and connected_count <= 0:
        prefix = "External MCP failed"
        level = "failed"
    elif failed_count:
        prefix = "External MCP ready" if ready_count > 0 else "External MCP available"
        level = "warning"
    elif ready_count > 0:
        prefix = "External MCP ready"
        level = "ready"
    elif connected_count > 0:
        prefix = "External MCP available"
        level = "ready"
    else:
        prefix = "External MCP failed"
        level = "failed"

    parts = [prefix, f"{connected_count}/{len(items)} servers"]
    if total_tools > 0:
        parts.append(f"{total_tools} tools")
    if total_filtered > 0:
        parts.append(f"{total_filtered} filtered")

    return McpStatusView(
        summary=summary_override or " · ".join(parts),
        level=level,
        done=done,
        details=_failure_details(items, limit=detail_limit) if done else (),
    )


def _failure_details(
    items: tuple[dict[str, typing.Any], ...],
    *,
    limit: int,
) -> tuple[McpStatusDetail, ...]:
    """生成受数量限制的失败服务详情。"""
    failed = tuple(
        item for item in items
        if str(item.get("state") or "").strip().lower() == "failed"
    )
    if not failed or limit <= 0:
        return ()

    visible_limit = max(0, min(int(limit), len(failed)))
    visible_count = visible_limit + (1 if len(failed) > visible_limit else 0)
    details = [
        McpStatusDetail(
            text=f"{_detail_connector(index, visible_count)}{_item_text(item)}",
            state="failed",
        )
        for index, item in enumerate(failed[:visible_limit])
    ]

    remaining = len(failed) - visible_limit
    if remaining > 0:
        details.append(McpStatusDetail(
            text=f"{_detail_connector(visible_limit, visible_count)}... {remaining} more servers",
            state="more",
        ))
    return tuple(details)


def _item_text(item: dict[str, typing.Any]) -> str:
    """生成单个 MCP 服务的状态文本。"""
    name = str(item.get("name") or "server").strip() or "server"
    detail = str(item.get("detail") or "").strip()
    return f"{name}: {detail or 'failed'}"


def _detail_connector(index: int, count: int) -> str:
    """返回详情行使用的树形连接符。"""
    if count <= 1:
        return "  └ "
    return "  └ " if index >= count - 1 else "  ├ "

MCP_STATUS_BODY_STYLE    = TextStyle(foreground="#DDE7EF")
MCP_STATUS_WARNING_STYLE = TextStyle(foreground="#F59E0B")


def render_mcp_status_block(
    view: McpStatusView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把已结束的 MCP 状态转换为稳定展示块。"""
    summary = str(view.summary or "").strip()
    if not view.done or not summary:
        return StyledBlock(plain_text="")

    marker_style = {
        "ready": SUCCESS_DOT_STYLE,
        "warning": MCP_STATUS_WARNING_STYLE,
        "failed": ERROR_DOT_STYLE,
    }.get(view.level, MCP_STATUS_BODY_STYLE)
    summary_style = (
        ERROR_PREVIEW_MESSAGE_STYLE
        if view.level == "failed"
        else MCP_STATUS_BODY_STYLE
    )

    summary_lines = _content_lines(summary)
    if not summary_lines:
        return StyledBlock(plain_text="")
    parts = layout_styled_line(
        [TextSpan(summary_lines[0], summary_style)],
        first_prefix=TextSpan("■ ", marker_style),
        continuation_prefix=TextSpan("  ", summary_style),
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    for line in summary_lines[1:]:
        _append_line(
            parts,
            line,
            style=summary_style,
            first_prefix="  ",
            continuation_prefix="  ",
            terminal_width=terminal_width,
            measure_width=measure_width,
        )

    for detail in view.details:
        detail_style = (
            PREVIEW_MORE_STYLE
            if detail.state == "more"
            else ERROR_PREVIEW_MESSAGE_STYLE
        )
        detail_lines = _content_lines(detail.text)
        if not detail_lines:
            continue
        prefix, continuation, first = _detail_prefix(detail_lines[0])
        _append_line(
            parts,
            first,
            style=detail_style,
            first_prefix=prefix,
            continuation_prefix=continuation,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
        for line in detail_lines[1:]:
            _append_line(
                parts,
                line,
                style=detail_style,
                first_prefix=continuation,
                continuation_prefix=continuation,
                terminal_width=terminal_width,
                measure_width=measure_width,
            )

    spans = tuple(parts)
    return StyledBlock(
        plain_text="".join(part.text for part in spans),
        spans=spans,
        preserve_spans=True,
    )


def _append_line(
    parts: list[TextSpan],
    text: str,
    *,
    style: TextStyle,
    first_prefix: str,
    continuation_prefix: str,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None,
) -> None:
    """向 MCP 状态追加一条带树形悬挂缩进的逻辑行。"""
    parts.append(TextSpan("\n"))
    parts.extend(layout_styled_line(
        [TextSpan(text, style)],
        first_prefix=TextSpan(first_prefix, style),
        continuation_prefix=TextSpan(continuation_prefix, style),
        terminal_width=terminal_width,
        measure_width=measure_width,
    ))


def _content_lines(value: typing.Any) -> list[str]:
    """清理 MCP 展示文本并移除边界空白行。"""
    lines = sanitize_terminal_text(value).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _detail_prefix(text: str) -> tuple[str, str, str]:
    """拆分 MCP 详情中的树形前缀并返回对应续行前缀。"""
    if text.startswith("  ├ "):
        return "  ├ ", "  │ ", text[4:]
    if text.startswith("  └ "):
        return "  └ ", "    ", text[4:]
    return "  └ ", "    ", text


if __name__ == '__main__':
    pass
