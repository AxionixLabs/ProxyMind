# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from engine.observability import (
    observe,
    observe_exception
)
from .commands import (
    AgentListenCommand,
    BatchCommand,
    ExecCommand,
    InteractiveCommand,
    RuntimeCommand
)
from ..modes.result import RunResult

if typing.TYPE_CHECKING:
    from ..controller import Mind


async def run_selected_mode(
    mind: "Mind",
    command: RuntimeCommand,
) -> RunResult | None:
    """按命令行参数分派到直接执行或交互模式。"""
    if isinstance(command, AgentListenCommand):
        selected_mode = "agent"
        access_mode   = "safe"
    elif isinstance(command, (ExecCommand, BatchCommand)):
        selected_mode = command.mode
        access_mode   = command.access_mode
    elif isinstance(command, InteractiveCommand):
        selected_mode = "tui"
        access_mode   = "safe"
    else:
        raise TypeError(f"unsupported runtime command: {type(command).__name__}")

    started_at = time.perf_counter()

    observe("mode.start", mode=selected_mode, access_mode=access_mode)

    run_result: RunResult | None = None

    try:
        if isinstance(command, AgentListenCommand):
            await mind.agent_loop()
        elif isinstance(command, ExecCommand):
            run_result = await mind.calling(
                message=command.prompt,
                mode=command.mode,
                access_mode=access_mode,
            )
            mind.exit_code = run_result.exit_code
        elif isinstance(command, BatchCommand):
            run_result = await mind.mind_pack(
                list(command.sources),
                command.mode,
                access_mode=access_mode,
            )
            mind.exit_code = run_result.exit_code
        elif isinstance(command, InteractiveCommand):
            from ..tui.session.loop import run_tui_loop

            for image in command.images:
                mind.attach.add_pending_attachments(image)
            await run_tui_loop(
                mind,
                initial_prompt=command.prompt,
                initial_images=command.images,
                initial_model=command.model,
            )
    except asyncio.CancelledError:
        observe(
            "mode.interrupted",
            level="WARNING",
            mode=selected_mode,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        observe_exception(
            "mode.failed",
            error,
            mode=selected_mode,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    else:
        observe(
            "mode.complete",
            mode=selected_mode,
            outcome=run_result.status if run_result is not None else None,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
    return run_result


if __name__ == '__main__':
    pass
