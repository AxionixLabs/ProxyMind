# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.frontend import ApplicationView
from ..core.runtime import require_tui_runtime
from ..core.submission import TuiInterruptRequested
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


async def run_tui_loop(
    mind: "Mind",
    *,
    initial_prompt: str | None = None,
    initial_images: tuple[str, ...] = (),
    initial_model: str | None = None,
) -> None:
    """运行 TUI 输入、命令分派和模型轮次生命周期。"""
    application = mind.frontend.application
    runtime     = require_tui_runtime(mind.frontend.runtime)

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
            finally:
                if not prompt_task.done():
                    prompt_task.cancel()
                await asyncio.gather(prompt_task, return_exceptions=True)

            prompt_text = prompt_task.result()
            action = await dispatcher.dispatch(prompt_text)
        if action is DispatchAction.EXIT:
            break
        if action is DispatchAction.HANDLED:
            continue

        await state.refresh_preferences(mind, ttl_sec=0.0)
        application.emit(ApplicationView(type="tui.gap"))
        mind.native_coding.reset_patch_diff()

        await execute_tui_model_turn(
            application,
            runtime,
            run_tui_model_turn(
                mind,
                message_text=prompt_text,
                run_mode=state.mode,
                pref_config=state.pref_config,
                access_mode=state.access_mode,
            ),
            stream_command_handler=foreground_tasks.handle_stream_command,
            show_interrupt_notice=lambda: not mind.task_event.is_set(),
        )
        exit_reason = runtime.consume_exit_request()
        if exit_reason is not None:
            if exit_reason == "interrupt":
                mind.exit_code = 130
            mind.task_event.set()
            break


if __name__ == '__main__':
    pass
