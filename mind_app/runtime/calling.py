# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from loguru import logger
from mind_app.mcp import McpSessionLike
from mind_nova.events import EventReport
from mind_nova.modes import (
    DEFAULT_RUN_MODE, RunMode
)
from mind_nova import const
from ..stream_ui import StreamUI
from ..stream_events.worked import print_worked_footer

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


def resolve_mode_runner(
    mind: "Mind",
    mode: RunMode
) -> typing.Callable[..., typing.Awaitable[None]]:
    """根据单次调用模式选择底层执行器。"""
    if mode in {"chat", "fast", "xtra"}:
        return mind.stream_looper
    if mode == "plan":
        return mind.static_looper
    raise ValueError(f"Unsupported mode: {mode}")


async def run_mode_lifecycle(
    mind: "Mind",
    runner: typing.Callable[..., typing.Awaitable[None]],
    *,
    mode: RunMode = DEFAULT_RUN_MODE,
    **kwargs
) -> None:
    """为模式执行增加动画生命周期和耗时输出。"""
    started_at = time.perf_counter()

    await mind.start_anim(mode)

    try:
        await runner(mode=mode, **kwargs)
    finally:
        await mind.await_cleanup(mind.stop_anim())

    print_worked_footer(time.perf_counter() - started_at)


async def wakeup(
    mind: "Mind",
    session: McpSessionLike,
    stream_ui: typing.Optional[StreamUI] = None
) -> typing.Optional[str]:
    """按 TTL 触发设备刷新，避免高频重复 refresh。"""

    if ((now := time.time()) - mind.last_refresh_ts) < mind.ttl_sec:
        tip = f"ttl-hit: skip refresh ttl={mind.ttl_sec:.3f}s"
        if stream_ui and mind.level != const.SHOW_LEVEL:
            return await stream_ui.feed(tip, display=StreamUI.BLOCK)
        return logger.debug(tip)

    result  = await session.call_tool("refresh", {"ttl_sec": mind.ttl_sec})
    ok      = not result.isError
    content = result.content[0].text

    if not ok:
        return content

    mind.last_refresh_ts = now

    if stream_ui and mind.level != const.SHOW_LEVEL:
        return await stream_ui.feed(content, display=StreamUI.BLOCK)
    return logger.debug(content)


async def calling(
    mind: "Mind",
    pref_config: typing.Optional[dict[str, typing.Any]] = None,
    *,
    message: str,
    mode: RunMode = DEFAULT_RUN_MODE,
    **kwargs
) -> None:
    """统一包装一次用户调用，并由 mode 决定底层执行器。"""
    if not str(message or "").strip():
        return None

    if pref_config is None:
        pref_config = await mind.fresh_pref_config(ttl_sec=0.0)

    runner = resolve_mode_runner(mind, mode)

    meta_in = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
    cid     = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid     = meta_in.get("sid") if isinstance(meta_in, dict) else None

    kwargs["metadata"] = meta = {
        **meta_in,
        **mind.begin_session(cid=cid, sid=sid, mode=mode, title=message, source="calling")
    }

    owns_event_report = False

    event_report = kwargs.get("ev_report")
    if not event_report:
        event_report = EventReport(mode, meta["cid"], meta["sid"])
        kwargs["ev_report"] = event_report
        await event_report.open()
        owns_event_report = True

    async def function(
        session: McpSessionLike,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]]
    ) -> None:
        """在共享 MCP 会话中执行单次请求。"""
        await run_mode_lifecycle(
            mind,
            runner,
            session=session,
            mode=mode,
            pref_config=pref_config,
            message=message,
            openai_tools=openai_tools,
            tool_meta=tool_meta,
            **kwargs
        )

    try:
        return await mind.with_mcp_session(pref_config, function)
    finally:
        if owns_event_report:
            await event_report.flush()
            await event_report.close()


if __name__ == '__main__':
    pass
