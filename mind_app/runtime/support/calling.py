# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from mind_nova.events import EventReport
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from ...modes.result import RunResult
from ...stream_events.worked import emit_worked_footer
from engine.observability import (
    observe,
    observe_exception
)

if typing.TYPE_CHECKING:
    from mind_app.mcp.contracts import McpSessionLike
    from mind_app.controller import Mind


def resolve_mode_runner(
    mind: "Mind",
    mode: RunMode
) -> typing.Callable[..., typing.Awaitable[RunResult]]:
    """根据单次调用模式选择底层执行器。"""
    if mode in {"chat", "fast", "xtra"}:
        return mind.stream_looper
    raise ValueError(f"Unsupported mode: {mode}")


async def run_mode_lifecycle(
    mind: "Mind",
    runner: typing.Callable[..., typing.Awaitable[RunResult]],
    *,
    mode: RunMode = DEFAULT_RUN_MODE,
    **kwargs
) -> RunResult:
    """为模式执行增加动画生命周期和耗时输出。"""
    started_at = time.perf_counter()

    frontend_runtime = mind.frontend.runtime
    frontend_runtime.begin_terminal_progress()

    try:
        await mind.start_anim(mode)
        try:
            result = await runner(mode=mode, **kwargs)
        finally:
            await mind.await_cleanup(mind.stop_anim("wait"))
    finally:
        frontend_runtime.end_terminal_progress()

    if mind.animate:
        emit_worked_footer(
            mind.frontend.application,
            time.perf_counter() - started_at,
        )
    return result


async def calling(
    mind: "Mind",
    pref_config: typing.Optional[dict[str, typing.Any]] = None,
    *,
    message: str,
    mode: RunMode = DEFAULT_RUN_MODE,
    **kwargs
) -> RunResult:
    """统一包装一次用户调用，并由 mode 决定底层执行器。"""
    if not str(message or "").strip():
        return RunResult(status="failed", error="message is empty")

    if pref_config is None:
        pref_config = await mind.fresh_pref_config(ttl_sec=0.0)

    runner = resolve_mode_runner(mind, mode)

    meta_in = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
    cid     = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid     = meta_in.get("sid") if isinstance(meta_in, dict) else None

    kwargs["metadata"] = meta = {
        **meta_in,
        **mind.begin_session(cid=cid, sid=sid, title=message, source="calling")
    }

    started_at = time.perf_counter()

    observe(
        "call.start",
        mode=mode,
        cid=meta["cid"],
        sid=meta["sid"],
        message_chars=len(message),
        access_mode=kwargs.get("access_mode"),
    )

    owns_event_report = False

    event_report = kwargs.get("ev_report")
    if not event_report:
        event_report = EventReport(mode, meta["cid"], meta["sid"])
        kwargs["ev_report"] = event_report
        await event_report.open()
        owns_event_report = True

    async def function(
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]],
    ) -> RunResult:
        """在共享 MCP 会话中执行单次请求。"""
        return await run_mode_lifecycle(
            mind,
            runner,
            session=session,
            mode=mode,
            pref_config=pref_config,
            message=message,
            tools=tools,
            **kwargs
        )

    try:
        result = await mind.with_mcp_session(pref_config, function)
    except asyncio.CancelledError:
        observe(
            "call.interrupted",
            level="WARNING",
            mode=mode,
            cid=meta["cid"],
            sid=meta["sid"],
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        observe_exception(
            "call.failed",
            error,
            mode=mode,
            cid=meta["cid"],
            sid=meta["sid"],
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    else:
        observe(
            "call.complete",
            mode=mode,
            cid=meta["cid"],
            sid=meta["sid"],
            outcome=result.status,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return result
    finally:
        if owns_event_report:
            await event_report.flush()
            await event_report.close()


if __name__ == '__main__':
    pass
