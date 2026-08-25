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
from mind_nova import const
from mind_core.permissions import PermissionSettings
from ...runtime.execution import (
    AgentContext,
    TurnContext
)
from ...runtime.turns.executor import (
    TurnExecution,
    build_turn_input_payload,
    execute_turn,
    resolve_turn_hook_scope
)
from ..runtime.ports import TurnRuntimePort
from .turn_input import TuiTurnInputControl
from ..core.styles import (
    BODY_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    fragment_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ...runtime.turns.result import RunResult


def emit_tui_interrupt_notice(application: ApplicationSink) -> None:
    """提交一条与终端交互约定一致的会话中断提示。"""
    application.emit(ApplicationView(
        type="tui.interrupted",
        renderable=fragment_block(
            TextSpan("■", FAILURE_STYLE),
            TextSpan(" Conversation interrupted", BODY_STYLE),
            TextSpan(
                f" · Tell {const.APP_DESC} what to do differently.",
                MUTED_STYLE,
            ),
        ),
    ))


async def execute_tui_model_turn(
    application: ApplicationSink,
    runtime: TurnRuntimePort,
    turn: typing.Coroutine[typing.Any, typing.Any, "RunResult | None"],
    *,
    turn_input_control: TuiTurnInputControl | None = None,
    stream_command_handler: typing.Callable[
        [str, typing.Callable[[], bool]],
        bool,
    ] | None = None,
    show_interrupt_notice: typing.Callable[[], bool] = lambda: True
) -> "RunResult | None":
    """执行可由主输入区定向取消的单个模型轮次。"""
    runtime.set_turn_start_pending(True)
    task = asyncio.create_task(turn, name="tui model turn")

    application_failure = asyncio.create_task(
        runtime.wait_for_application_failure(),
        name="tui application failure",
    )

    interrupted: bool = False

    fatal_error: BaseException | None = None

    def cancel_turn() -> bool:
        """取消模型任务并记录响应中断来源。"""
        cancelled = (
            turn_input_control.interrupt(task.cancel)
            if turn_input_control is not None
            else task.cancel()
        )
        if cancelled:
            runtime.request_turn_interrupt()
        return cancelled

    try:
        runtime.set_execution_active(True)
        runtime.set_turn_start_pending(False)
        runtime.bind_interrupt_handler(cancel_turn)

        if turn_input_control is not None:
            runtime.bind_turn_input_handler(turn_input_control.submit)
            runtime.bind_queued_restore_handler(turn_input_control.restore_draft)

        if stream_command_handler is not None:
            runtime.bind_stream_command_handler(
                lambda value: stream_command_handler(value, cancel_turn)
            )

        completed, _pending = await asyncio.wait(
            (task, application_failure),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if application_failure in completed:
            fatal_error = application_failure.result()
            result = None
        else:
            result = await task

    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        if not runtime.consume_turn_interrupt():
            raise

        interrupted = True
        result = None

    else:
        interrupted = bool(
            runtime.consume_turn_interrupt()
            or getattr(result, "status", "") == "interrupted"
        )

    finally:
        if not task.done():
            task.cancel()
        if not application_failure.done():
            application_failure.cancel()

        await asyncio.gather(
            task,
            application_failure,
            return_exceptions=True,
        )

        if not interrupted:
            runtime.consume_turn_interrupt()

        runtime.bind_stream_command_handler(None)
        runtime.bind_turn_input_handler(None)
        runtime.bind_interrupt_handler(None)

        if turn_input_control is not None:
            await turn_input_control.close()

        if not runtime.uncertain_steers_active:
            runtime.bind_queued_restore_handler(None)

        runtime.set_execution_active(False)
        runtime.set_turn_start_pending(False)

    if fatal_error is not None:
        raise fatal_error

    if interrupted and show_interrupt_notice():
        emit_tui_interrupt_notice(application)

    return result


async def run_tui_model_turn(
    mind: "Mind",
    *,
    message_text: str,
    pref_config: dict[str, typing.Any],
    permissions: PermissionSettings,
    turn_id: str | None = None,
    prompt_extras: typing.Mapping[str, typing.Any] | None = None,
    on_prompt_prepared: typing.Callable[
        [list[dict[str, typing.Any]]],
        None,
    ] | None = None,
    turn_input_control: TuiTurnInputControl | None = None,
    on_interrupt_acknowledged: typing.Callable[[], None] | None = None,
) -> None:
    """为单轮 TUI 输入准备上下文并执行统一模型流程。"""
    attachments: list[dict[str, typing.Any]] = []

    if mind.attach.has_pending_attachments():
        attachments = mind.attach.consume_pending_attachments()

    if on_prompt_prepared is not None:
        on_prompt_prepared(attachments)

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

    runner = mind.stream_turn
    extras = dict(prompt_extras or {})

    conversation_turn = await mind.begin_conversation_turn(
        title=session_title,
        source="tui",
    )
    turn_metadata = conversation_turn.metadata()

    turn_context = TurnContext.create(
        agent=AgentContext.root(turn_metadata["sid"]),
        cid=turn_metadata["cid"],
        sid=turn_metadata["sid"],
        source="tui",
        pref_config=pref_config,
        cwd=mind.history_workspace,
        permissions=permissions,
        output_record_path=str(mind.report.output_record_path or ""),
        transcript_path=mind.transcripts.path_for_session(turn_metadata["sid"]),
        turn_id=turn_id,
        session_started=conversation_turn.session_started,
        session_start_reason=conversation_turn.start_reason,
    )
    execution = TurnExecution(
        context=turn_context,
        message=message_text,
        hook_scope=resolve_turn_hook_scope(mind, turn_context),
        metadata=turn_metadata,
        additional_context=conversation_turn.additional_context,
        system_message=conversation_turn.system_message,
        input_payload=build_turn_input_payload(
            message_text,
            attachments=attachments,
            extras=extras,
        ),
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
        if turn_input_control is not None:
            prompt_kwargs["on_turn_input_context"] = turn_input_control.activate
            prompt_kwargs["on_turn_input_event"] = turn_input_control.handle_event
            prompt_kwargs["on_turn_stream_end"] = (
                turn_input_control.handle_stream_end
            )
        if on_interrupt_acknowledged is not None:
            prompt_kwargs["on_turn_interrupted"] = on_interrupt_acknowledged

        return await mind.run_turn_lifecycle(
            runner,
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
