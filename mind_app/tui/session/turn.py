# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from mind_app.mcp.contracts import McpSessionLike
from mind_app.presentation.models import TextSpan
from mind_nova.events import EventReport
from mind_nova.modes import RunMode
from mind_nova import const
from mind_core.permissions import PermissionSettings
from ...runtime.execution import (
    AgentContext,
    TurnContext
)
from ...runtime.support.calling import resolve_mode_runner
from ...runtime.turns.executor import (
    TurnExecution,
    execute_turn,
    resolve_turn_hook_scope
)
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BRIGHT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    fragment_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ...modes.result import RunResult


async def execute_tui_model_turn(
    application: ApplicationSink,
    runtime: TuiRuntime,
    turn: typing.Coroutine[typing.Any, typing.Any, None],
    *,
    stream_command_handler: typing.Callable[
        [str, typing.Callable[[], bool]],
        bool,
    ] | None = None,
    show_interrupt_notice: typing.Callable[[], bool] = lambda: True
) -> None:
    """执行可由主输入区定向取消的单个模型轮次。"""
    task = asyncio.create_task(turn, name="tui model turn")

    interrupted: bool = False

    def cancel_turn() -> bool:
        """取消模型任务并记录响应中断来源。"""
        cancelled = task.cancel()
        if cancelled:
            runtime.request_turn_interrupt()
        return cancelled

    runtime.set_execution_active(True)
    runtime.bind_interrupt_handler(cancel_turn)

    if stream_command_handler is not None:
        runtime.bind_stream_command_handler(
            lambda value: stream_command_handler(value, cancel_turn)
        )

    try:
        await task
    except asyncio.CancelledError:
        if not runtime.consume_turn_interrupt():
            raise
        interrupted = True
    else:
        interrupted = runtime.consume_turn_interrupt()

    finally:
        if not interrupted:
            runtime.consume_turn_interrupt()

        runtime.bind_stream_command_handler(None)
        runtime.bind_interrupt_handler(None)
        runtime.set_execution_active(False)

    if interrupted and show_interrupt_notice():
        application.emit(ApplicationView(
            type="tui.interrupted",
            renderable=fragment_block(
                TextSpan("■", FAILURE_STYLE),
                TextSpan(" Response interrupted", BRIGHT_STYLE),
                TextSpan(
                    f" · Tell {const.APP_DESC} what to do differently.",
                    MUTED_STYLE,
                ),
            ),
        ))


async def run_tui_model_turn(
    mind: "Mind",
    *,
    message_text: str,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any],
    permissions: PermissionSettings,
    turn_id: str | None = None,
    prompt_extras: typing.Mapping[str, typing.Any] | None = None
) -> None:
    """为单轮 TUI 输入准备上下文并执行统一模型流程。"""
    attachments: list[dict[str, typing.Any]] = []
    if mind.attach.has_pending_attachments():
        attachments = mind.attach.consume_pending_attachments()

    attachment_names = [
        str(attachment.get("filename") or "").strip()
        for attachment in attachments
        if str(attachment.get("filename") or "").strip()
    ]
    session_title = (
        message_text.strip()
        or ", ".join(attachment_names)
        or "Image"
    )

    runner = resolve_mode_runner(mind, run_mode)
    extras = dict(prompt_extras or {})

    conversation_turn = mind.begin_conversation_turn(
        title=session_title,
        source="tui",
    )
    turn_metadata = conversation_turn.metadata()

    turn_context = TurnContext.create(
        agent=AgentContext.root(turn_metadata["sid"]),
        cid=turn_metadata["cid"],
        sid=turn_metadata["sid"],
        mode=run_mode,
        source="tui",
        pref_config=pref_config,
        cwd=mind.history_workspace,
        permissions=permissions,
        turn_id=turn_id,
        session_started=conversation_turn.session_started,
        session_start_reason=conversation_turn.start_reason,
    )
    execution = TurnExecution(
        context=turn_context,
        message=message_text,
        hook_scope=resolve_turn_hook_scope(mind, turn_context),
        metadata=turn_metadata,
    )

    async def run_tui_turn(
        prepared: TurnExecution,
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport
    ) -> "RunResult":
        """使用 TUI 前端生命周期执行已经准备好的根轮次。"""
        prompt_kwargs: dict[str, typing.Any] = {}
        if extras:
            prompt_kwargs["extras"] = extras

        return await mind.run_mode_lifecycle(
            runner,
            mode=prepared.context.mode,
            session=session,
            pref_config=pref_config,
            tools=tools,
            attachments=attachments,
            ev_report=event_report,
            turn_execution=prepared,
            **prompt_kwargs,
        )

    await execute_turn(
        mind,
        pref_config,
        execution,
        run_tui_turn,
    )


if __name__ == '__main__':
    pass
