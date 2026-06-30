# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from .config import slugify_mcp_name


class ExternalMcpStatus(object):
    """维护外部 MCP 启动过程的可展示状态快照。"""

    def __init__(self, servers: list[dict[str, typing.Any]]) -> None:
        """根据启用的外部服务初始化状态项。"""
        self._items: dict[str, dict[str, typing.Any]] = {}
        self._updated_at = time.monotonic()
        self._done = False

        for server in servers or []:
            if not bool(server.get("enabled", True)):
                continue

            alias = slugify_mcp_name(server.get("name"), fallback="server")
            self._items[alias] = {
                "name"   : alias,
                "state"  : "linking",
                "tools"  : 0,
                "detail" : ""
            }

    @property
    def visible(self) -> bool:
        """是否存在需要展示启动状态的外部 MCP 服务。"""
        return bool(self._items)

    def mark_linking(self, server: dict[str, typing.Any]) -> None:
        """标记服务正在连接。"""
        self._update(server, "linking")

    def mark_failed(self, server: dict[str, typing.Any], reason: str) -> None:
        """标记服务连接或工具加载失败。"""
        self._update(server, "failed", detail=reason)

    def mark_ready(self, server: dict[str, typing.Any], alias: str, tool_count: int) -> None:
        """标记服务已连接，并记录最终别名与工具数量。"""
        state = "ready" if tool_count > 0 else "empty"
        self._update(server, state, alias=alias, tools=max(0, int(tool_count)), detail="")

    def finish(self) -> None:
        """标记整个外部 MCP 启动状态已结束。"""
        self._done = True
        self._updated_at = time.monotonic()

    def finish_unresolved(self, reason: str = "") -> None:
        """结束前把仍处于等待态的服务统一标记为失败。"""
        final_reason = str(reason or "").strip()
        for item in self._items.values():
            if str(item.get("state") or "").lower() in {"linking", "queued"}:
                item["state"] = "failed"
                if final_reason:
                    item["detail"] = final_reason
        self.finish()

    def snapshot(self) -> dict[str, typing.Any]:
        """返回供 UI 动画读取的状态快照。"""
        return {
            "done"       : self._done,
            "updated_at" : self._updated_at,
            "items"      : [dict(item) for item in self._items.values()]
        }

    def _update(
        self,
        server: dict[str, typing.Any],
        state: str,
        *,
        alias: str | None = None,
        tools: int | None = None,
        detail: str | None = None
    ) -> None:
        """按配置服务名定位状态项，并更新状态字段与刷新时间。"""
        key = slugify_mcp_name(server.get("name"), fallback="server")

        item = self._items.get(key)
        if item is None:
            return None

        if alias:
            item["name"] = slugify_mcp_name(alias, fallback=key)
        item["state"] = str(state or "unknown")
        if tools is not None:
            item["tools"] = max(0, int(tools))
        if detail is not None:
            item["detail"] = str(detail or "").strip()
        self._updated_at = time.monotonic()
        return None


def remaining_budget(deadline: float) -> float:
    """计算距离统一截止时间还剩多少秒，最小返回 0。"""
    return max(0.0, deadline - time.monotonic())


def flatten_exceptions(exc: BaseException) -> typing.Iterator[BaseException]:
    """展开 BaseExceptionGroup，便于按底层异常做判断。"""
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            yield from flatten_exceptions(item)
        return
    yield exc


def summarize_exception(exc: BaseException) -> str:
    """从异常组中挑一个可读异常摘要用于 debug 日志。"""
    for item in flatten_exceptions(exc):
        text = str(item).strip()
        if text:
            return f"{type(item).__name__}: {text}"

    return f"{type(exc).__name__}: {exc}"


def external_status_detail_from_exception(exc: BaseException | None = None) -> str:
    """返回展示给外部 MCP 状态 UI 的失败摘要。"""
    if exc is None:
        return "failed"
    return summarize_exception(exc)


def should_reraise_external(exc: BaseException) -> bool:
    """判断外部 MCP 异常是否必须继续抛出，避免吞掉退出和主动取消。"""
    current_task = asyncio.current_task()
    is_current_cancelled = current_task is not None and current_task.cancelling() > 0

    return any(
        isinstance(item, (KeyboardInterrupt, SystemExit))
        or (is_current_cancelled and isinstance(item, asyncio.CancelledError))
        for item in flatten_exceptions(exc)
    )


if __name__ == '__main__':
    pass
