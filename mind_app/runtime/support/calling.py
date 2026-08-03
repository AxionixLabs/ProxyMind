# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from ..execution import (
    AgentContext,
    TurnContext
)
from ..turns.executor import (
    TurnExecution,
    build_turn_input_payload,
    execute_turn,
    resolve_turn_hook_scope
)
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
        try:
            result = await runner(**kwargs)
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
    **kwargs
) -> RunResult:
    """统一包装一次用户调用。"""
    if not str(message or "").strip():
        return RunResult(status="failed", error="message is empty")

    if pref_config is None:
        pref_config = await mind.fresh_pref_config(ttl_sec=0.0)

    runner       = mind.stream_turn
    permissions  = kwargs.pop("permissions", None) or mind.permissions
    raw_metadata = kwargs.pop("metadata", None)
    meta_in      = raw_metadata if isinstance(raw_metadata, dict) else {}
    cid          = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid          = meta_in.get("sid") if isinstance(meta_in, dict) else None

    conversation_turn = await mind.begin_conversation_turn(
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
        source="calling",
        pref_config=pref_config,
        cwd=mind.history_workspace,
        permissions=permissions,
        output_record_path=str(mind.report.output_record_path or ""),
        transcript_path=mind.transcripts.path_for_session(meta["sid"]),
        turn_id=kwargs.pop("turn_id", None),
        session_started=conversation_turn.session_started,
        session_start_reason=conversation_turn.start_reason,
    )

    raw_attachments = kwargs.get("attachments")
    raw_extras      = kwargs.get("extras")

    execution = TurnExecution(
        context=turn_context,
        message=message,
        hook_scope=resolve_turn_hook_scope(mind, turn_context),
        metadata=meta,
        additional_context=conversation_turn.additional_context,
        system_message=conversation_turn.system_message,
        input_payload=build_turn_input_payload(
            message,
            attachments=(
                item
                for item in raw_attachments
                if isinstance(item, dict)
            ) if isinstance(raw_attachments, (list, tuple)) else (),
            extras=raw_extras if isinstance(raw_extras, dict) else None,
        ),
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
