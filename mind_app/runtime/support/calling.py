# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from ..execution import (
    AgentContext,
    TurnContext
)
from ..turns.executor import (
    TurnExecution,
    execute_turn
)
from ...modes.result import RunResult
from ...stream_events.worked import emit_worked_footer

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.mcp.contracts import McpSessionLike
    from mind_nova.events import EventReport


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

    completed: bool = False

    try:
        await mind.start_anim(mode)
        try:
            result = await runner(mode=mode, **kwargs)
        finally:
            await mind.await_cleanup(mind.stop_anim("wait"))

        completed = True

        return result

    finally:
        frontend_runtime.end_terminal_progress()
        if completed and mind.animate:
            emit_worked_footer(
                mind.frontend.application, time.perf_counter() - started_at
            )


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

    runner       = resolve_mode_runner(mind, mode)
    permissions  = kwargs.pop("permissions", None) or mind.permissions
    raw_metadata = kwargs.pop("metadata", None)
    meta_in      = raw_metadata if isinstance(raw_metadata, dict) else {}
    cid          = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid          = meta_in.get("sid") if isinstance(meta_in, dict) else None

    conversation_turn = mind.begin_conversation_turn(
        cid=cid,
        sid=sid,
        title=message,
        source="calling",
    )
    meta = {
        **meta_in,
        **conversation_turn.metadata(),
    }

    turn_context = TurnContext.create(
        agent=AgentContext.root(meta["sid"]),
        cid=meta["cid"],
        sid=meta["sid"],
        mode=mode,
        source="calling",
        pref_config=pref_config,
        cwd=mind.history_workspace,
        permissions=permissions,
        turn_id=kwargs.pop("turn_id", None),
        session_started=conversation_turn.session_started,
        session_start_reason=conversation_turn.start_reason,
    )
    execution = TurnExecution(
        context=turn_context,
        message=message,
        metadata=meta,
    )
    event_report = kwargs.pop("ev_report", None)

    async def run_root_turn(
        prepared: TurnExecution,
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]],
        report: "EventReport"
    ) -> RunResult:
        """使用主前端生命周期执行根模型轮次。"""
        return await run_mode_lifecycle(
            mind,
            runner,
            session=session,
            mode=prepared.context.mode,
            pref_config=pref_config,
            message=prepared.message,
            tools=tools,
            permissions=prepared.context.permissions,
            metadata=dict(prepared.metadata),
            turn_context=prepared.context,
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
