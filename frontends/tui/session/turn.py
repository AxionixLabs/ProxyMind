# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from observability import observe
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView
)
from agent.ports import (
    ApprovalLedger,
    EffectJournalFactory,
    ExecutionPolicy,
    McpSessionPort,
    ModelCapability,
    ProtocolCommandClient,
    PatchPreviewPort,
    RetryStatePort,
    TurnAnimationPort,
    TurnSessionContextPort,
    TurnSessionStatePort,
    TurnCleanupPort,
    TranscriptFactory,
)
from agent.ports.presentation import TextSpan
from protocol.transport.events import EventReport
from metadata import const
from agent.domain.policies import PermissionSettings
from agent.application.turns.execution import TurnExecution
from mind_app.runtime.turns.executor import execute_turn
from mind_app.runtime.turns.root import prepare_root_turn
from mind_app.presentation.terminal.turn_lifecycle import run_foreground_turn
from mind_app.presentation.output import SessionFactory
from mind_app.runtime.turns.stream import stream_turn
from ..runtime.ports import TurnRuntimePort
from ..core.interrupt import InterruptDisposition
from .turn_input import TuiTurnInputControl
from ..core.styles import (
    BODY_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    fragment_block
)

if typing.TYPE_CHECKING:
    from agent.application.turns.run_result import RunResult
    from ...controller import Mind

TurnValue = typing.TypeVar("TurnValue")


class _TurnInterruptState(object):
    """保存单次活动轮次的用户中断来源。"""

    def __init__(self) -> None:
        self.requested = False

    def request(self) -> None:
        """记录当前轮次已收到用户中断。"""
        self.requested = True


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


def _turn_result_status(result: object) -> str:
    """优先读取 Event Queue 生成的稳定终态投影。"""
    projection = getattr(result, "projection", None)
    projected_status = getattr(projection, "status", None)
    if isinstance(projected_status, str):
        return projected_status
    status = getattr(result, "status", None)
    return status if isinstance(status, str) else ""


async def execute_tui_model_turn(
    application: ApplicationSink,
    runtime: TurnRuntimePort,
    turn: typing.Coroutine[typing.Any, typing.Any, TurnValue],
    *,
    turn_input_control: TuiTurnInputControl | None = None,
    stream_command_handler: typing.Callable[
        [str, typing.Callable[[], InterruptDisposition]],
        bool,
    ] | None = None,
    show_interrupt_notice: typing.Callable[[], bool] = lambda: True
) -> TurnValue | None:
    """执行可由主输入区定向取消的单个模型轮次。"""
    runtime.set_turn_start_pending(True)
    task = asyncio.create_task(turn, name="tui model turn")

    application_failure = asyncio.create_task(
        runtime.wait_for_application_failure(),
        name="tui application failure",
    )

    interrupt_state = _TurnInterruptState()

    fatal_error: BaseException | None = None

    def cancel_turn() -> InterruptDisposition:
        """先取消本地模型任务，再异步同步远端中断。"""
        if task.done():
            return InterruptDisposition.IGNORED

        interrupt_state.request()
        task.cancel()

        observe(
            "tui.turn.interrupt.local",
            task_name=task.get_name(),
            remote_control=turn_input_control is not None,
        )

        if turn_input_control is not None:
            turn_input_control.request_interrupt()

        return InterruptDisposition.CONSUMED

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
        if not interrupt_state.requested:
            raise

        interrupted = True
        result = None

    else:
        interrupted = bool(
            interrupt_state.requested
            or _turn_result_status(result) == "interrupted"
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
    attachments: typing.Iterable[typing.Mapping[str, typing.Any]] | None = None,
    environment_snapshot: typing.Mapping[str, typing.Any] | None = None,
    turn_id: str | None = None,
    prompt_extras: typing.Mapping[str, typing.Any] | None = None,
    model_capability: ModelCapability | None = None,
    protocol_client: ProtocolCommandClient | None = None,
    effect_journal_factory: EffectJournalFactory | None = None,
    execution_policy: ExecutionPolicy | None = None,
    approval_ledger: ApprovalLedger | None = None,
    session_factory: SessionFactory | None = None,
    transcript_factory: TranscriptFactory | None = None,
    cleanup: TurnCleanupPort | None = None,
    patch_preview: PatchPreviewPort | None = None,
    retry_state: RetryStatePort | None = None,
    animation: TurnAnimationPort | None = None,
    session_context: TurnSessionContextPort | None = None,
    session_state: TurnSessionStatePort | None = None,
    on_prompt_prepared: typing.Callable[
        [list[dict[str, typing.Any]]],
        None,
    ] | None = None,
    turn_input_control: TuiTurnInputControl | None = None,
    on_interrupt_acknowledged: typing.Callable[[], None] | None = None,
) -> "RunResult":
    """为单轮 TUI 输入准备上下文并执行统一模型流程。"""
    tool_filter_mode = mind.tool_profile_for_turn()
    if attachments is None:
        attachment_values = (
            mind.attach.consume_pending_attachments()
            if mind.attach.has_pending_attachments()
            else []
        )
    else:
        attachment_values = [dict(item) for item in attachments]
        if mind.attach.has_pending_attachments():
            mind.attach.consume_pending_attachments()

    if on_prompt_prepared is not None:
        on_prompt_prepared(attachment_values)

    attachment_names = [
        str(attachment.get("filename") or "").strip()
        for attachment in attachment_values
        if str(attachment.get("filename") or "").strip()
    ]
    session_title = (
        message_text.strip()
        or ", ".join(attachment_names)
        or "Image"
    )

    extras = dict(prompt_extras or {})

    execution = await prepare_root_turn(
        mind,
        message=message_text,
        title=session_title,
        source="tui",
        pref_config=pref_config,
        permissions=permissions,
        metadata={},
        attachments=attachment_values,
        extras=extras,
        approval_ledger=approval_ledger,
        execution_policy=execution_policy,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        patch_preview=patch_preview,
        retry_state=retry_state,
        animation=animation,
        session_context=session_context,
        session_state=session_state,
        turn_id=turn_id,
    )

    async def run_tui_turn(
        prepared: TurnExecution,
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport
    ) -> "RunResult":
        """使用 TUI 前端生命周期执行已经准备好的根轮次。"""
        prompt_kwargs: dict[str, typing.Any] = {
            "exec_env": (
                dict(environment_snapshot)
                if environment_snapshot is not None
                else None
            ),
        }
        if extras:
            prompt_kwargs["extras"] = extras
        if model_capability is not None:
            prompt_kwargs["model_capability"] = model_capability
        if protocol_client is not None:
            prompt_kwargs["protocol_client"] = protocol_client
        if effect_journal_factory is not None:
            prompt_kwargs["effect_journal_factory"] = effect_journal_factory
        if session_factory is not None:
            prompt_kwargs["session_factory"] = session_factory
        if turn_input_control is not None:
            prompt_kwargs["on_turn_input_context"] = turn_input_control.activate
            prompt_kwargs["on_turn_input_event"] = turn_input_control.handle_event
            prompt_kwargs["on_turn_stream_end"] = (
                turn_input_control.handle_stream_end
            )
        if on_interrupt_acknowledged is not None:
            prompt_kwargs["on_turn_interrupted"] = on_interrupt_acknowledged

        return await run_foreground_turn(
            mind,
            stream_turn,
            mind,
            session=session,
            pref_config=pref_config,
            tools=tools,
            attachments=attachment_values,
            ev_report=event_report,
            turn_execution=prepared,
            **prompt_kwargs,
        )

    return await execute_turn(
        mind,
        pref_config,
        execution,
        run_tui_turn,
        tool_filter_mode=tool_filter_mode,
    )


if __name__ == '__main__':
    pass
