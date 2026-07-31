# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.frontend import ApplicationView
from mind_nova.identifiers import short_uid
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from ..core.models import TranscriptBacktrackRequest
from ..core.submission import (
    TuiInterruptRequested,
    TuiTranscriptBacktrackRequested
)
from ..features.conversation import (
    ForkLiveStatus,
    finish_fork_activity,
    fork_current_conversation,
    render_fork_failure,
    render_fork_interrupted,
    render_fork_result
)
from ..features.processes import monitor_exec_status
from .barriers import TuiForegroundTasks
from .dispatch import (
    DispatchAction,
    TuiCommandDispatcher
)
from .state import TuiSessionState
from .turn import (
    execute_tui_model_turn,
    run_tui_model_turn
)

if typing.TYPE_CHECKING:
    from ...controller import Mind


def _pending_attachment_snapshot(
    attachment_state: typing.Any
) -> tuple[dict[str, typing.Any], ...]:
    """读取并固定待提交附件快照的结构。"""
    reader = getattr(
        attachment_state,
        "pending_attachments_snapshot",
        None,
    )
    if not callable(reader):
        return ()

    values = reader()
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        dict(item)
        for item in values
        if isinstance(item, dict)
    )


async def run_tui_loop(
    mind: "Mind",
    *,
    initial_prompt: str | None = None,
    initial_images: tuple[str, ...] = (),
    initial_model: str | None = None
) -> None:
    """运行 TUI 输入、命令分派和模型轮次生命周期。"""
    application = mind.frontend.application
    runtime     = require_tui_runtime(mind.frontend.runtime)

    attachment_state = getattr(mind, "attach", None)

    attachment_check = getattr(
        attachment_state,
        "has_pending_attachments",
        None,
    )
    runtime.bind_pending_attachment_check(
        attachment_check if callable(attachment_check) else None
    )

    runtime.start_background_task(
        monitor_exec_status(runtime, mind),
        name="process status",
    )

    state = TuiSessionState.create(
        mind,
        runtime,
        model_override=initial_model,
    )
    foreground_tasks = TuiForegroundTasks(runtime, mind)

    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        state,
        foreground_tasks,
    )
    if initial_prompt is not None:
        runtime.submissions.enqueue_message(initial_prompt)
    attachment_start_pending = initial_prompt is None and bool(initial_images)

    while not mind.task_event.is_set():
        await foreground_tasks.wait()
        if mind.task_event.is_set():
            break

        if attachment_start_pending:
            attachment_start_pending = False
            await state.refresh_for_prompt(mind)
            state.apply_prompt_context(runtime)
            prompt_text = ""
            action = DispatchAction.MODEL_TURN
        else:
            prompt_task = asyncio.create_task(
                mind.frontend.interaction.read_message(state.prompt_context()),
                name="tui read message",
            )
            try:
                await state.refresh_for_prompt(mind)
                state.apply_prompt_context(runtime)
                await prompt_task
            except TuiInterruptRequested:
                mind.exit_code = 130
                mind.task_event.set()
                break
            except EOFError:
                mind.task_event.set()
                break
            except UnicodeDecodeError:
                continue
            except TuiTranscriptBacktrackRequested as requested:
                await _handle_transcript_backtrack(
                    mind,
                    runtime,
                    state,
                    foreground_tasks,
                    requested.request,
                )
                continue
            finally:
                if not prompt_task.done():
                    prompt_task.cancel()
                await asyncio.gather(prompt_task, return_exceptions=True)

            prompt_text = prompt_task.result()

            try:
                action = (
                    DispatchAction.MODEL_TURN
                    if not prompt_text.strip() and runtime.has_pending_attachments
                    else await dispatcher.dispatch(prompt_text)
                )
            finally:
                runtime.discard_pending_submission()

        if action is DispatchAction.EXIT:
            break
        if action is DispatchAction.HANDLED:
            continue

        await state.refresh_preferences(mind, ttl_sec=0.0)
        application.emit(ApplicationView(type="tui.gap"))

        mind.native_coding.reset_patch_diff()

        turn_id = short_uid(12)

        attachment_snapshot = _pending_attachment_snapshot(attachment_state)

        runtime.bind_submitted_turn(
            turn_id,
            prompt_text,
            has_attachments=bool(attachment_snapshot),
            attachment_labels=(
                str(item.get("filename") or "")
                for item in attachment_snapshot
            ),
        )

        await execute_tui_model_turn(
            application,
            runtime,
            run_tui_model_turn(
                mind,
                message_text=prompt_text,
                run_mode=state.mode,
                pref_config=state.pref_config,
                permissions=state.permissions,
                turn_id=turn_id,
                prompt_extras=state.consume_pending_prompt_extras(),
            ),
            stream_command_handler=dispatcher.handle_stream_command,
            show_interrupt_notice=lambda: not mind.task_event.is_set(),
        )
        exit_reason = runtime.consume_exit_request()
        if exit_reason is not None:
            if exit_reason == "interrupt":
                mind.exit_code = 130
            mind.task_event.set()
            break


async def _handle_transcript_backtrack(
    mind: "Mind",
    runtime: TuiRuntime,
    state: TuiSessionState,
    foreground_tasks: TuiForegroundTasks,
    request: TranscriptBacktrackRequest
) -> None:
    """通过前台屏障执行历史分支并恢复选中的输入。"""
    runtime.replace_input_text(request.prompt)

    foreground_tasks.start(
        "Conversation backtrack",
        lambda: fork_current_conversation(
            mind,
            run_mode=state.mode,
            before_turn_id=request.turn_id,
        ),
        finish_activity=lambda: finish_fork_activity(mind),
        on_succeeded=lambda status: _finish_transcript_backtrack(
            mind,
            runtime,
            state,
            request,
            status,
        ),
        on_failed=lambda error: render_fork_failure(mind, error),
        on_cancelled=lambda: render_fork_interrupted(mind),
    )
    await foreground_tasks.wait()


def _finish_transcript_backtrack(
    mind: "Mind",
    runtime: TuiRuntime,
    state: TuiSessionState,
    request: TranscriptBacktrackRequest,
    status: ForkLiveStatus
) -> None:
    """在远端分支结束后同步本地正文或展示失败。"""
    if status.succeeded:
        prompt = status.prompt
        if prompt is None:
            render_fork_failure(
                mind,
                RuntimeError("Conversation fork did not return a prompt."),
            )
            return None

        mind.attach.replace_pending_attachments(prompt.attachments)
        state.replace_pending_prompt_extras(prompt.extras)

        canonical_request = TranscriptBacktrackRequest(
            turn_id=request.turn_id,
            prompt=prompt.message,
        )

        if not runtime.apply_transcript_backtrack(canonical_request):
            render_fork_failure(
                mind,
                RuntimeError("Selected transcript turn is no longer available."),
            )
        return None

    render_fork_result(mind, status)


if __name__ == '__main__':
    pass
