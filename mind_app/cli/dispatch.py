# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from engine.errors import AppError
from mind_core.preference import apply_primary_model_override
from engine.observability import (
    observe,
    observe_exception
)
from .commands import (
    AgentListenCommand,
    ExecCommand,
    InteractiveCommand,
    ResumeCommand,
    RuntimeCommand
)
from ..history import (
    HISTORY_LIMIT,
    INTERACTIVE_HISTORY_SOURCES
)
from ..runtime.turns.result import RunResult

if typing.TYPE_CHECKING:
    from ..controller import Mind


async def run_selected_command(
    mind: "Mind",
    command: RuntimeCommand
) -> RunResult | None:
    """按命令行参数分派到直接执行或交互入口。"""
    if isinstance(command, AgentListenCommand):
        command_name = "agent"
    elif isinstance(command, ExecCommand):
        command_name = "exec"
    elif isinstance(command, (InteractiveCommand, ResumeCommand)):
        command_name = "tui"
    else:
        raise TypeError(f"unsupported runtime command: {type(command).__name__}")

    started_at = time.perf_counter()

    observe(
        "command.start",
        command=command_name,
        sandbox_mode=mind.permissions.sandbox_mode,
        approval_policy=mind.permissions.approval_policy,
    )

    run_result: RunResult | None = None

    try:
        if isinstance(command, AgentListenCommand):
            await _run_agent_listener_session(mind)
        elif isinstance(command, ExecCommand):
            attachments: list[dict[str, typing.Any]] = []
            if command.images:
                for image in command.images:
                    mind.attach.add_pending_attachments(image)
                attachments = mind.attach.consume_pending_attachments()

            calling_kwargs: dict[str, typing.Any] = {}
            if command.model is not None:
                calling_kwargs["pref_config"] = apply_primary_model_override(
                    await mind.fresh_pref_config(ttl_sec=0.0),
                    command.model,
                )

            run_result = await mind.calling(
                message=command.prompt,
                attachments=attachments,
                **calling_kwargs,
            )
            mind.exit_code = run_result.exit_code
        elif isinstance(command, InteractiveCommand):
            await _run_tui_session(
                mind,
                prompt=command.prompt,
                images=command.images,
                model=command.model,
            )
        elif isinstance(command, ResumeCommand):
            record = await _select_resume_session(mind, command)
            if record is None:
                mind.task_event.set()
            else:
                resumed = await mind.resume_conversation(
                    record,
                    source="tui:resume",
                )
                if resumed is None:
                    raise AppError("Session could not be resumed.")
                await _run_tui_session(
                    mind,
                    prompt=command.prompt,
                    images=command.images,
                    model=command.model,
                )
    except asyncio.CancelledError:
        observe(
            "command.interrupted",
            level="WARNING",
            command=command_name,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        observe_exception(
            "command.failed",
            error,
            command=command_name,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    else:
        observe(
            "command.complete",
            command=command_name,
            outcome=run_result.status if run_result is not None else None,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
    return run_result


async def _run_agent_listener_session(mind: "Mind") -> None:
    """在普通 TUI 生命周期内运行临时远端请求监听器。"""
    from ..subscription.runtime import AgentRuntime

    listener = AgentRuntime(mind)
    listener.start_background()

    try:
        await _run_tui_session(
            mind,
            prompt=None,
            images=(),
            model=None,
        )
    finally:
        await listener.stop()


async def _run_tui_session(
    mind: "Mind",
    *,
    prompt: str | None,
    images: tuple[str, ...],
    model: str | None
) -> None:
    """使用现有 TUI 生命周期运行一个交互会话。"""
    from ..tui.session.loop import run_tui_loop

    for image in images:
        mind.attach.add_pending_attachments(image)
    await run_tui_loop(
        mind,
        initial_prompt=prompt,
        initial_images=images,
        initial_model=model,
    )


async def _select_resume_session(
    mind: "Mind",
    command: ResumeCommand
) -> dict[str, typing.Any] | None:
    """按命令条件查找或选择一个可恢复会话。"""
    sources = (
        None
        if command.include_non_interactive
        else INTERACTIVE_HISTORY_SOURCES
    )

    workspace = None if command.all_workspaces else mind.history_workspace

    if command.session_id is not None:
        record = mind.find_conversation_session(
            command.session_id,
            workspace=workspace,
            sources=sources,
        )
        if record is None:
            raise AppError(
                "Session is unavailable for the selected working directory."
            )
        return record

    records = mind.recent_conversation_sessions(
        workspace=workspace,
        sources=sources,
        limit=1 if command.last else HISTORY_LIMIT,
    )
    if not records:
        raise AppError("No resumable sessions were found.")
    if command.last:
        return records[0]

    from ..tui.core.runtime import require_tui_runtime
    from ..tui.features.history import choose_history_session

    return await choose_history_session(
        require_tui_runtime(mind.frontend.runtime),
        records,
        show_workspace=command.all_workspaces,
    )


if __name__ == '__main__':
    pass
