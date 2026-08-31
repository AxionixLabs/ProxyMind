# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import functools
import time
import typing

from agent.adapters.turns.root import RootTurnCommandExecutor
from agent.application.config.session_identity import derive_local_session_id
from agent.application.turns.commands import (
    SubmitTurnCommand,
    TurnApplication,
)
from agent.application.turns.run_result import RunResult
from infrastructure.config.preferences import apply_primary_model_override
from infrastructure.config.runtime_paths import agent_runtime_db_path
from infrastructure.errors import AppError
from observability import (
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
from mind_app.history import (
    HISTORY_LIMIT,
    INTERACTIVE_HISTORY_SOURCES
)
from mind_app.runtime.turns.root import (
    run_root_turn,
)
from mind_app.interaction.environment import capture_active_turn_environment

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


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
    run_outcome: str | None = None

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

            durable_runtime = getattr(mind, "application_layout", None) is not None
            turn_application = (
                mind.runtime_services.create_turn_application(
                    agent_runtime_db_path()
                )
                if durable_runtime
                else TurnApplication()
            )
            local_session_id = None
            if durable_runtime:
                local_session_id = derive_local_session_id(
                    "cli",
                    mind.conversation.snapshot(),
                )
            environment_snapshot = capture_active_turn_environment(mind)
            submit_command = SubmitTurnCommand.create(
                session_id=local_session_id,
                message=command.prompt,
                attachments=attachments,
                environment_snapshot=environment_snapshot,
                pref_config=calling_kwargs.get("pref_config"),
            )

            execute_root_turn = RootTurnCommandExecutor(
                functools.partial(run_root_turn, mind),
                include_empty_attachments=True,
            )

            try:
                execution = await turn_application.submit(
                    submit_command,
                    execute_root_turn,
                )
            finally:
                await turn_application.close(cancel_running=True)
            run_result = execution.value
            run_outcome = execution.projection.status
            mind.exit_code = execution.projection.exit_code
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
                from mind_app.tui.core.runtime import require_tui_runtime
                from mind_app.tui.features.history import load_history_transcript

                runtime    = require_tui_runtime(mind.frontend.runtime)
                session_id = str(record.get("sid") or "").strip()

                replay_blocks = await asyncio.to_thread(
                    load_history_transcript,
                    mind,
                    session_id,
                    terminal_width=runtime.terminal_width,
                    hyperlinks=runtime.hyperlinks_enabled,
                    terminal_capabilities=runtime.terminal_capabilities,
                    record=record,
                )

                resumed = await mind.resume_conversation(
                    record,
                    source="tui:resume",
                )
                if resumed is None:
                    raise AppError("Session could not be resumed.")
                runtime.replace_transcript(replay_blocks)

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
            outcome=run_outcome,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

    return run_result


async def _run_agent_listener_session(mind: "Mind") -> None:
    """在普通 TUI 生命周期内运行临时远端请求监听器。"""
    mind.subscription.start()
    await _run_tui_session(
        mind,
        prompt=None,
        images=(),
        model=None,
    )


async def _run_tui_session(
    mind: "Mind",
    *,
    prompt: str | None,
    images: tuple[str, ...],
    model: str | None
) -> None:
    """使用现有 TUI 生命周期运行一个交互会话。"""
    from mind_app.tui.session.loop import run_tui_loop

    for image in images:
        mind.attach.add_pending_attachments(image)

    try:
        await run_tui_loop(
            mind,
            initial_prompt=prompt,
            initial_images=images,
            initial_model=model,
        )
    finally:
        await mind.subscription.close()


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
            status="active",
        )
        if record is None:
            raise AppError(
                "Session is unavailable for the selected working directory."
            )
        return record

    history_kwargs: dict[str, typing.Any] = {
        "workspace": workspace,
        "sources": sources,
        "limit": 1 if command.last else HISTORY_LIMIT,
    }
    if command.last:
        history_kwargs["status"] = "active"
    records = mind.recent_conversation_sessions(**history_kwargs)
    if command.last:
        if not records:
            raise AppError("No resumable sessions were found.")
        return records[0]

    from mind_app.tui.core.runtime import require_tui_runtime
    from mind_app.tui.features.history import (
        HistoryResumePreviewLoader,
        HistoryResumeTranscriptLoader,
        choose_history_session
    )
    from mind_app.tui.contracts.resume import ResumeRow, ResumeSessionStatus
    from dataclasses import replace

    async def archive_session(row: ResumeRow) -> None:
        """归档 CLI picker 中的活动会话。"""
        await mind.archive_conversation_session(row.cid, row.sid)

    async def unarchive_session(row: ResumeRow) -> ResumeRow:
        """恢复 CLI picker 中选择的 archived 会话。"""
        await mind.unarchive_conversation(row.cid, row.sid)
        return replace(row, status=ResumeSessionStatus.ACTIVE)

    runtime = require_tui_runtime(mind.frontend.runtime)

    return await choose_history_session(
        runtime,
        records,
        filter_workspace=mind.history_workspace,
        show_workspace=command.all_workspaces,
        preview_loader=HistoryResumePreviewLoader(
            mind,
            terminal_capabilities=runtime.terminal_capabilities,
        ),
        transcript_loader=HistoryResumeTranscriptLoader(
            mind,
            terminal_capabilities=runtime.terminal_capabilities,
        ),
        archive_session=archive_session,
        unarchive_session=unarchive_session,
    )


if __name__ == '__main__':
    pass
