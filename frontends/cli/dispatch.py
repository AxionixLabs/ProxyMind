# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import functools
import time
import typing

from agent.adapters.turns.root import RootTurnCommandExecutor
from agent.application.config.session_identity import derive_local_session_id
from agent.application.services import TurnApplicationFactory
from agent.application.turns.commands import (
    SubmitTurnCommand,
    TurnApplication,
)
from agent.application.turns.run_result import RunResult
from agent.ports import (
    ProcessLifecyclePort,
    ProtocolCommandClient,
    RootConversationPort,
)
from agent.stores.sessions import (
    HISTORY_LIMIT,
    INTERACTIVE_HISTORY_SOURCES,
)
from frontends.runtime import Frontend
from frontends.tui.features.conversation import ConversationCompactor
from infrastructure.config.preferences import apply_primary_model_override
from infrastructure.config.runtime_paths import agent_runtime_db_path
from infrastructure.errors import AppError
from observability import (
    observe,
    observe_exception,
)
from protocol.schema.identifiers import short_uid
from .commands import (
    AgentListenCommand,
    ExecCommand,
    InteractiveCommand,
    ResumeCommand,
    RuntimeCommand,
)


class _AttachmentState(typing.Protocol):
    """描述 CLI 冻结输入附件所需的前端状态。"""

    def add_pending_attachments(self, raw_path: str) -> dict[str, typing.Any]:
        """登记一项待提交附件。"""
        ...

    def consume_pending_attachments(self) -> list[dict[str, typing.Any]]:
        """读取并清空待提交附件。"""
        ...


class _SubscriptionSession(typing.Protocol):
    """描述 CLI 临时订阅会话的生命周期。"""

    def start(self) -> object:
        """启动订阅监听。"""
        ...

    async def close(self) -> None:
        """关闭订阅监听及其资源。"""
        ...


class CliCommandHost(typing.Protocol):
    """描述 CLI 命令分发所需的最小应用宿主。"""

    attach: _AttachmentState
    conversation: RootConversationPort
    frontend: Frontend
    history_workspace: str
    lifecycle: ProcessLifecyclePort
    subscription: _SubscriptionSession


class RootTurnRunner(typing.Protocol):
    """定义组合根提供的 CLI 根轮次执行能力。"""

    async def __call__(
        self,
        controller: CliCommandHost,
        *,
        message: str,
        **kwargs: typing.Any,
    ) -> RunResult:
        """执行一次已冻结的根轮次。"""
        ...


class EnvironmentSnapshotProvider(typing.Protocol):
    """定义组合根提供的 CLI 环境快照能力。"""

    def __call__(
        self,
        controller: CliCommandHost,
    ) -> dict[str, typing.Any] | None:
        """捕获当前 CLI Turn 使用的不可变环境快照。"""
        ...


async def _require_turn_runner(
    _controller: CliCommandHost,
    *,
    message: str,
    **_kwargs: typing.Any,
) -> RunResult:
    """在 CLI 未由组合根装配时返回明确配置错误。"""
    _ = message
    raise RuntimeError("CLI root turn runner is required")


def _require_environment_snapshot(
    _controller: CliCommandHost,
) -> dict[str, typing.Any] | None:
    """在 CLI 未由组合根装配时返回明确配置错误。"""
    raise RuntimeError("CLI environment snapshot provider is required")


run_root_turn: RootTurnRunner = _require_turn_runner
capture_active_turn_environment: EnvironmentSnapshotProvider = (
    _require_environment_snapshot
)


async def run_selected_command(
    host: CliCommandHost,
    command: RuntimeCommand,
    *,
    protocol_client: ProtocolCommandClient,
    turn_runner: RootTurnRunner | None = None,
    environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
    turn_application_factory: TurnApplicationFactory | None = None,
    conversation_compactor: ConversationCompactor | None = None,
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
        sandbox_mode=host.conversation.permissions.sandbox_mode,
        approval_policy=host.conversation.permissions.approval_policy,
    )

    run_result: RunResult | None = None
    run_outcome: str | None = None

    try:
        if isinstance(command, AgentListenCommand):
            await _run_agent_listener_session(
                host,
                turn_runner=turn_runner,
                turn_application_factory=turn_application_factory,
                conversation_compactor=conversation_compactor,
                protocol_client=protocol_client,
            )
        elif isinstance(command, ExecCommand):
            attachments: list[dict[str, typing.Any]] = []
            if command.images:
                for image in command.images:
                    host.attach.add_pending_attachments(image)
                attachments = host.attach.consume_pending_attachments()

            calling_kwargs: dict[str, typing.Any] = {}
            if command.model is not None:
                calling_kwargs["pref_config"] = apply_primary_model_override(
                    await host.conversation.fresh_pref_config(ttl_sec=0.0),
                    command.model,
                )

            durable_runtime = getattr(host, "application_layout", None) is not None
            if durable_runtime:
                if turn_application_factory is None:
                    raise RuntimeError(
                        "CLI turn application factory is required"
                    )
                turn_application = turn_application_factory(
                    agent_runtime_db_path()
                )
            else:
                turn_application = TurnApplication()
            local_session_id = None
            if durable_runtime:
                local_session_id = derive_local_session_id(
                    "cli",
                    host.conversation.snapshot(),
                )
            environment_snapshot = (
                capture_active_turn_environment
                if environment_snapshot_provider is None
                else environment_snapshot_provider
            )(host)
            command_extras: dict[str, typing.Any] = {}
            trace_context: dict[str, typing.Any] = {}
            if durable_runtime:
                remote_session = host.conversation.snapshot()
                turn_id = short_uid(12)
                command_extras["turn_id"] = turn_id
                trace_context["remote_turn"] = {
                    "cid": remote_session["cid"],
                    "sid": remote_session["sid"],
                    "turn_id": turn_id,
                }
            submit_command = SubmitTurnCommand.create(
                session_id=local_session_id,
                message=command.prompt,
                attachments=attachments,
                environment_snapshot=environment_snapshot,
                pref_config=calling_kwargs.get("pref_config"),
                extras=command_extras,
                trace_context=trace_context,
            )

            execute_root_turn = RootTurnCommandExecutor(
                functools.partial(
                    run_root_turn if turn_runner is None else turn_runner,
                    host,
                ),
                include_empty_attachments=True,
                request_recorder=(
                    turn_application if durable_runtime else None
                ),
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
            host.lifecycle.set_exit_code(execution.projection.exit_code)
        elif isinstance(command, InteractiveCommand):
            await _run_tui_session(
                host,
                prompt=command.prompt,
                images=command.images,
                model=command.model,
                turn_runner=turn_runner,
                turn_application_factory=turn_application_factory,
                conversation_compactor=conversation_compactor,
                protocol_client=protocol_client,
            )
        elif isinstance(command, ResumeCommand):
            record = await _select_resume_session(host, command)
            if record is None:
                host.lifecycle.request_stop()
            else:
                from frontends.tui.core.runtime import require_tui_runtime
                from frontends.tui.features.history import load_history_transcript

                runtime = require_tui_runtime(host.frontend.runtime)
                session_id = str(record.get("sid") or "").strip()

                replay_blocks = await asyncio.to_thread(
                    load_history_transcript,
                    host,
                    session_id,
                    terminal_width=runtime.terminal_width,
                    hyperlinks=runtime.hyperlinks_enabled,
                    terminal_capabilities=runtime.terminal_capabilities,
                    record=record,
                )

                resumed = await host.conversation.resume(
                    record,
                    source="tui:resume",
                )
                if resumed is None:
                    raise AppError("Session could not be resumed.")
                runtime.replace_transcript(replay_blocks)

                await _run_tui_session(
                    host,
                    prompt=command.prompt,
                    images=command.images,
                    model=command.model,
                    turn_runner=turn_runner,
                    turn_application_factory=turn_application_factory,
                    conversation_compactor=conversation_compactor,
                    protocol_client=protocol_client,
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


async def _run_agent_listener_session(
    host: CliCommandHost,
    *,
    turn_runner: RootTurnRunner | None,
    turn_application_factory: TurnApplicationFactory | None,
    conversation_compactor: ConversationCompactor | None,
    protocol_client: ProtocolCommandClient,
) -> None:
    """在普通 TUI 生命周期内运行临时远端请求监听器。"""
    host.subscription.start()
    await _run_tui_session(
        host,
        prompt=None,
        images=(),
        model=None,
        turn_runner=turn_runner,
        turn_application_factory=turn_application_factory,
        conversation_compactor=conversation_compactor,
        protocol_client=protocol_client,
    )


async def _run_tui_session(
    host: CliCommandHost,
    *,
    prompt: str | None,
    images: tuple[str, ...],
    model: str | None,
    turn_runner: RootTurnRunner | None,
    turn_application_factory: TurnApplicationFactory | None,
    conversation_compactor: ConversationCompactor | None,
    protocol_client: ProtocolCommandClient,
) -> None:
    """使用现有 TUI 生命周期运行一个交互会话。"""
    from frontends.tui.session.loop import run_tui_loop

    for image in images:
        host.attach.add_pending_attachments(image)

    try:
        loop_kwargs: dict[str, typing.Any] = {
            "initial_prompt": prompt,
            "initial_images": images,
            "initial_model": model,
            "turn_runner": functools.partial(
                run_root_turn if turn_runner is None else turn_runner,
                host,
            ),
            "protocol_client": protocol_client,
        }
        if turn_application_factory is not None:
            loop_kwargs["turn_application_factory"] = turn_application_factory
        if conversation_compactor is not None:
            loop_kwargs["conversation_compactor"] = conversation_compactor
        await run_tui_loop(host, **loop_kwargs)
    finally:
        await host.subscription.close()


async def _select_resume_session(
    host: CliCommandHost,
    command: ResumeCommand
) -> dict[str, typing.Any] | None:
    """按命令条件查找或选择一个可恢复会话。"""
    sources = (
        None
        if command.include_non_interactive
        else INTERACTIVE_HISTORY_SOURCES
    )

    workspace = None if command.all_workspaces else host.history_workspace

    if command.session_id is not None:
        record = host.conversation.history.find(
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
    records = host.conversation.history.recent(**history_kwargs)
    if command.last:
        if not records:
            raise AppError("No resumable sessions were found.")
        return records[0]

    from frontends.tui.core.runtime import require_tui_runtime
    from frontends.tui.features.history import (
        HistoryResumePreviewLoader,
        HistoryResumeTranscriptLoader,
        choose_history_session
    )
    from frontends.tui.contracts.resume import ResumeRow, ResumeSessionStatus
    from dataclasses import replace

    async def archive_session(row: ResumeRow) -> None:
        """归档 CLI picker 中的活动会话。"""
        await host.conversation.archive(row.cid, row.sid)

    async def unarchive_session(row: ResumeRow) -> ResumeRow:
        """恢复 CLI picker 中选择的 archived 会话。"""
        await host.conversation.history.unarchive(cid=row.cid, sid=row.sid)
        return replace(row, status=ResumeSessionStatus.ACTIVE)

    runtime = require_tui_runtime(host.frontend.runtime)

    return await choose_history_session(
        runtime,
        records,
        filter_workspace=host.history_workspace,
        show_workspace=command.all_workspaces,
        preview_loader=HistoryResumePreviewLoader(
            host,
            terminal_capabilities=runtime.terminal_capabilities,
        ),
        transcript_loader=HistoryResumeTranscriptLoader(
            host,
            terminal_capabilities=runtime.terminal_capabilities,
        ),
        archive_session=archive_session,
        unarchive_session=unarchive_session,
    )


if __name__ == '__main__':
    pass
