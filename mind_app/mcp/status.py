# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from .config import slugify_mcp_name

EXTERNAL_MCP_HEALTH: dict[str, tuple[float, str]] = {}


class ExternalMcpStatus(object):

    def __init__(self, servers: list[dict[str, typing.Any]]) -> None:
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
        return bool(self._items)

    def mark_linking(self, server: dict[str, typing.Any]) -> None:
        self._update(server, "linking")

    def mark_cached(self, server: dict[str, typing.Any], reason: str = "") -> None:
        self._update(server, "cached", detail=reason)

    def mark_failed(self, server: dict[str, typing.Any], reason: str) -> None:
        self._update(server, "failed", detail=reason)

    def mark_ready(self, server: dict[str, typing.Any], alias: str, tool_count: int) -> None:
        state = "ready" if tool_count > 0 else "empty"
        self._update(server, state, alias=alias, tools=max(0, int(tool_count)), detail="")

    def finish(self) -> None:
        self._done = True
        self._updated_at = time.monotonic()

    def finish_unresolved(self, reason: str = "") -> None:
        final_reason = str(reason or "").strip()
        for item in self._items.values():
            if str(item.get("state") or "").lower() in {"linking", "queued"}:
                item["state"] = "failed"
                if final_reason:
                    item["detail"] = final_reason
        self.finish()

    def snapshot(self) -> dict[str, typing.Any]:
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


def server_health_key(server: dict[str, typing.Any]) -> str:
    name = str(server.get("name") or "server").strip()
    transport = str(server.get("transport") or "streamable_http").strip()
    url = str(server.get("url") or "").strip()
    return f"{name}|{transport}|{url}"


def cached_failure_reason(server: dict[str, typing.Any]) -> str | None:
    key = server_health_key(server)
    cached = EXTERNAL_MCP_HEALTH.get(key)

    if cached is None:
        return None

    failed_until, reason = cached

    if failed_until <= time.monotonic():
        EXTERNAL_MCP_HEALTH.pop(key, None)
        return None

    return reason


def mark_server_failure(server: dict[str, typing.Any]) -> None:
    EXTERNAL_MCP_HEALTH[server_health_key(server)] = (
        time.monotonic() + 20.0,
        "failed",
    )


def mark_server_success(server: dict[str, typing.Any]) -> None:
    EXTERNAL_MCP_HEALTH.pop(server_health_key(server), None)


def remaining_budget(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def flatten_exceptions(exc: BaseException) -> typing.Iterator[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            yield from flatten_exceptions(item)
        return
    yield exc


def summarize_exception(exc: BaseException) -> str:
    for item in flatten_exceptions(exc):
        text = str(item).strip()
        if text:
            return f"{type(item).__name__}: {text}"

    return f"{type(exc).__name__}: {exc}"


def external_status_detail_from_exception() -> str:
    return "failed"


def should_reraise_external(exc: BaseException) -> bool:
    current_task = asyncio.current_task()
    is_current_cancelled = current_task is not None and current_task.cancelling() > 0

    return any(
        isinstance(item, (KeyboardInterrupt, SystemExit))
        or (is_current_cancelled and isinstance(item, asyncio.CancelledError))
        for item in flatten_exceptions(exc)
    )


if __name__ == '__main__':
    pass
