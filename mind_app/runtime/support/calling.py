# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from ..turns.executor import (
    TurnExecution,
    execute_turn,
)
from ..turns.root import prepare_root_turn
from ..turns.result import RunResult
from ...stream_events.worked import emit_worked_footer

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.mcp.contracts import McpSessionLike
    from mind_nova.events import EventReport


async def run_turn_lifecycle(
    mind: "Mind",
    runner: typing.Callable[..., typing.Awaitable[RunResult]],
    **kwargs
) -> RunResult:
    """为单轮执行增加动画生命周期和耗时输出。"""
    started_at = time.perf_counter()

    frontend_runtime = mind.frontend.runtime
    frontend_runtime.begin_terminal_progress()

    completed: bool = False

    try:
        await mind.start_anim()
        result = await runner(**kwargs)

        completed = True

        return result

    finally:
        try:
            if completed:
                finish_turn_wait = getattr(
                    frontend_runtime,
                    "finish_turn_wait",
                    None,
                )
                if callable(finish_turn_wait):
                    finish_turn_wait()
            if completed and mind.animate:
                emit_worked_footer(
                    mind.frontend.application, time.perf_counter() - started_at
                )
        finally:
            try:
                await mind.await_cleanup(mind.stop_anim("wait"))
            finally:
                frontend_runtime.end_terminal_progress()


async def calling(
    mind: "Mind",
    pref_config: typing.Optional[dict[str, typing.Any]] = None,
    *,
    message: str,
    **kwargs
) -> RunResult:
    """统一包装一次用户调用。"""
    if not str(message or "").strip():
        return RunResult(status="failed", error="message is empty")

    if pref_config is None:
        pref_config = await mind.fresh_pref_config(ttl_sec=0.0)

    runner = mind.stream_turn
    permissions = kwargs.pop("permissions", None) or mind.permissions
    raw_metadata = kwargs.pop("metadata", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_attachments = kwargs.get("attachments")
    attachments = (
        tuple(item for item in raw_attachments if isinstance(item, dict))
        if isinstance(raw_attachments, (list, tuple))
        else ()
    )
    raw_extras = kwargs.get("extras")
    execution = await prepare_root_turn(
        mind,
        message=message,
        title=message,
        source="calling",
        pref_config=pref_config,
        permissions=permissions,
        metadata=metadata,
        attachments=attachments,
        extras=raw_extras if isinstance(raw_extras, dict) else None,
        turn_id=kwargs.pop("turn_id", None),
    )
    event_report = kwargs.pop("ev_report", None)

    async def run_root_turn(
        prepared: TurnExecution,
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]],
        report: "EventReport"
    ) -> RunResult:
        """使用主前端生命周期执行根模型轮次。"""
        return await run_turn_lifecycle(
            mind,
            runner,
            session=session,
            pref_config=pref_config,
            tools=tools,
            turn_execution=prepared,
            ev_report=report,
            **kwargs
        )

    return await execute_turn(
        mind,
        pref_config,
        execution,
        run_root_turn,
        event_report=event_report,
    )


if __name__ == '__main__':
    pass
