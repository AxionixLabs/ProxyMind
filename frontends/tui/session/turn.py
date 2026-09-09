# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections.abc import Mapping

from agent.domain.policies import PermissionSettings
from agent.ports import ModelRequestFrozenCallback
from agent.ports import RunRecoveryRequired
from agent.application.turns.observation import TurnObservationCallbacks
from agent.protocol import ReviewStreamRequest
from agent.protocol.json_value import ThawedJsonValue
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
)
from agent.ports.presentation import TextSpan
from metadata import const
from observability import observe
from .turn_input import TuiTurnInputControl
from ..core.interrupt import InterruptDisposition
from ..core.styles import (
    BODY_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    fragment_block,
)
from ..runtime.ports import TurnRuntimePort

if typing.TYPE_CHECKING:
    from agent.application.turns.run_result import RunResult

TurnValue = typing.TypeVar("TurnValue")


class TuiRootTurnRunner(typing.Protocol):
    """描述 TUI 调用已绑定根轮次用例的稳定入口。

    实现方必须在组合根固定模型、协议、工具、报告和清理生命周期；TUI 只提交
    当前输入快照与展示回调，不得组装具体执行器。
    """

    async def __call__(
        self,
        pref_config: dict[str, typing.Any] | None = None,
        *,
        message: str,
        **kwargs: typing.Any,
    ) -> "RunResult":
        """执行一次已冻结的 TUI 根轮次。"""
        ...


class TuiReviewTurnRunner(typing.Protocol):
    """描述 TUI 调用组合根 Review 用例的稳定入口。"""

    async def __call__(
        self,
        pref_config: dict[str, typing.Any],
        *,
        request: ReviewStreamRequest,
        environment_snapshot: Mapping[str, ThawedJsonValue] | None,
        hint: str,
        callbacks: TurnObservationCallbacks | None = None,
        replay_target_seq: int | None = None,
    ) -> "RunResult":
        """提交或观察一次身份和工具集合均已冻结的 Review。"""
        ...


class _TurnInterruptState(object):
    """保存单次活动轮次的用户中断来源。"""

    def __init__(self) -> None:
        self.requested = False

    def request(self) -> None:
        """记录当前轮次已收到用户中断。"""
        self.requested = True


def emit_tui_interrupt_notice(
    application: ApplicationSink,
    *,
    submit_pending_steers: bool = False,
) -> None:
    """提交一条与终端交互约定一致的会话中断提示。"""
    if submit_pending_steers:
        application.emit(ApplicationView(
            type="tui.interrupted",
            renderable=fragment_block(
                TextSpan("• ", BODY_STYLE),
                TextSpan(
                    "Model interrupted to submit steer instructions.",
                    BODY_STYLE,
                ),
            ),
        ))
        return None

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


def emit_tui_recovery_notice(
    application: ApplicationSink,
    error: RunRecoveryRequired,
) -> None:
    """把本地 Run 恢复阻塞转换为可继续交互的提示。"""
    first = error.details[0] if error.details else None
    suffix = (
        f" ({first.run_id}: {first.action})"
        if first is not None
        else ""
    )
    application.emit(ApplicationView(
        type="turn.recovery_required",
        renderable=fragment_block(
            TextSpan("■", FAILURE_STYLE),
            TextSpan(
                " Previous turn requires recovery before this command can run"
                f"{suffix}.",
                BODY_STYLE,
            ),
        ),
        payload={
            "recoveries": tuple(
                {
                    "run_id": detail.run_id,
                    "status": detail.status,
                    "action": detail.action,
                    "effect_id": detail.effect_id,
                }
                for detail in error.details
            ),
        },
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
    interrupted: bool = False
    submit_pending_steers: bool = False

    def cancel_local_stream() -> None:
        """取消当前轮次持有的本地事件流。"""
        if not task.done():
            task.cancel()

    def cancel_turn() -> InterruptDisposition:
        """登记中断，并继续观察远端权威终态。"""
        if interrupt_state.requested:
            if turn_input_control is not None:
                turn_input_control.abandon()
            cancel_local_stream()
            return InterruptDisposition.EXIT_REQUESTED
        if task.done():
            return InterruptDisposition.IGNORED

        interrupt_state.request()

        if turn_input_control is None:
            interrupt_dispatched = False
            cancel_local_stream()
        else:
            interrupt_dispatched = turn_input_control.request_interrupt()
        runtime.begin_interrupt_settlement()

        observe(
            "tui.turn.interrupt.local",
            task_name=task.get_name(),
            remote_control=turn_input_control is not None,
            interrupt_dispatched=interrupt_dispatched,
            local_cancelled=turn_input_control is None,
        )

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

    except RunRecoveryRequired as error:
        emit_tui_recovery_notice(application, error)
        result = None
        interrupted = False

    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        if not interrupt_state.requested:
            raise

        interrupted = True
        result = None

    else:
        terminal_status = _turn_result_status(result)
        interrupted = bool(
            terminal_status in {"interrupted", "cancelled"}
            or (interrupt_state.requested and not terminal_status)
        )

    finally:
        if interrupted:
            submit_pending_steers = (
                runtime.submit_pending_steers_after_interrupt
            )
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
        try:
            if turn_input_control is not None:
                await turn_input_control.close()
                if interrupted:
                    runtime.restore_interrupted_submissions()
                else:
                    runtime.clear_pending_steer_interrupt_intent()
            else:
                runtime.clear_pending_steer_interrupt_intent()
        finally:
            if interrupted:
                runtime.finish_interrupted_presentation()
            runtime.bind_interrupt_handler(None)

            if not runtime.uncertain_steers_active:
                runtime.bind_queued_restore_handler(None)

            runtime.set_execution_active(False)
            runtime.set_turn_start_pending(False)

    if fatal_error is not None:
        raise fatal_error

    if interrupted and show_interrupt_notice():
        emit_tui_interrupt_notice(
            application,
            submit_pending_steers=submit_pending_steers,
        )

    return result


async def run_tui_model_turn(
    turn_runner: TuiRootTurnRunner,
    *,
    message_text: str,
    pref_config: dict[str, typing.Any],
    permissions: PermissionSettings,
    attachments: typing.Iterable[typing.Mapping[str, typing.Any]] = (),
    environment_snapshot: typing.Mapping[str, typing.Any] | None = None,
    turn_id: str | None = None,
    prompt_extras: typing.Mapping[str, typing.Any] | None = None,
    on_prompt_prepared: typing.Callable[
                            [list[dict[str, typing.Any]]],
                            None,
                        ] | None = None,
    turn_input_control: TuiTurnInputControl | None = None,
    on_model_request_frozen: ModelRequestFrozenCallback | None = None,
) -> "RunResult":
    """冻结 TUI 输入并提交给组合根绑定的根轮次用例。"""
    attachment_values = [dict(item) for item in attachments]

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

    prompt_kwargs: dict[str, typing.Any] = {
        "title": session_title,
        "source": "tui",
        "permissions": permissions,
        "attachments": attachment_values,
        "exec_env": (
            dict(environment_snapshot)
            if environment_snapshot is not None
            else None
        ),
        "turn_id": turn_id,
    }
    if extras:
        prompt_kwargs["extras"] = extras
    if turn_input_control is not None:
        prompt_kwargs["on_turn_input_context"] = turn_input_control.activate
        prompt_kwargs["on_turn_input_event"] = turn_input_control.handle_event
        prompt_kwargs["on_turn_stream_end"] = turn_input_control.handle_stream_end
    if on_model_request_frozen is not None:
        prompt_kwargs["on_model_request_frozen"] = on_model_request_frozen
    return await turn_runner(
        pref_config,
        message=message_text,
        **prompt_kwargs,
    )


if __name__ == '__main__':
    pass
