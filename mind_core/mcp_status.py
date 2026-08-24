# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

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
        return McpStatusView(
            f"{title} failed",
            "failed",
            True,
            details,
        )
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

        summary = f"{summary_override} · {detail}" if summary_override and detail else summary_override

        return McpStatusView(
            summary=summary,
            level="ready" if done else "running",
            done=done,
        )

    ready_count: int     = 0
    connected_count: int = 0
    failed_count: int    = 0
    total_tools: int     = 0
    total_filtered: int  = 0

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
        level  = "running"
    elif failed_count and connected_count <= 0:
        prefix = "External MCP failed"
        level  = "failed"
    elif failed_count:
        prefix = "External MCP ready" if ready_count > 0 else "External MCP available"
        level  = "warning"
    elif ready_count > 0:
        prefix = "External MCP ready"
        level  = "ready"
    elif connected_count > 0:
        prefix = "External MCP available"
        level  = "ready"
    else:
        prefix = "External MCP failed"
        level  = "failed"

    parts = [
        prefix,
        f"{connected_count}/{len(items)} servers",
    ]
    if total_tools > 0:
        parts.append(f"{total_tools} tools")
    if total_filtered > 0:
        parts.append(f"{total_filtered} filtered")

    details = _failure_details(items, limit=detail_limit) if done else ()

    return McpStatusView(
        summary=summary_override or " · ".join(parts),
        level=level,
        done=done,
        details=details,
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
    name   = str(item.get("name") or "server").strip() or "server"
    detail = str(item.get("detail") or "").strip()
    return f"{name}: {detail or 'failed'}"


def _detail_connector(index: int, count: int) -> str:
    """返回详情行使用的树形连接符。"""
    if count <= 1:
        return "  └ "
    return "  └ " if index >= count - 1 else "  ├ "


if __name__ == '__main__':
    pass
