# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from agent.application.config.session_identity import derive_local_session_id
from agent.application.services import TurnApplicationFactory
from agent.application.turns.commands import (
    SessionRecoveryResult,
    SubmitTurnCommand,
    TurnApplication,
)
from agent.application.turns.run_result import RunResult
from agent.ports import (
    AttachmentStatePort,
    ProtocolCommandClient,
    RunRecoveryRequired,
)
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
)
from infrastructure.config.runtime_paths import agent_runtime_db_path
from infrastructure.services.turn_environment import (
    capture_active_turn_environment,
)
from protocol.client.fork import ResubmittablePrompt
from protocol.schema.identifiers import short_uid
from .barriers import TuiForegroundTasks
from .dispatch import (
    DispatchAction,
    TuiCommandDispatcher
)
from .state import TuiSessionState
from .turn import (
    TuiRootTurnRunner,
    emit_tui_interrupt_notice,
    execute_tui_model_turn,
    run_tui_model_turn
)
from .turn_input import TuiTurnInputControl
from ..core.models import (
    MailboxRunRequest,
    TranscriptBacktrackRequest
)
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from ..core.queued import TuiSubmission
from ..core.submission import (
    TuiInterruptRequested,
    TuiMailboxRunRequested,
    TuiTranscriptBacktrackRequested
)
from ..features.conversation import (
    ConversationCompactor,
    ForkLiveStatus,
    fork_current_conversation,
    render_fork_failure,
    render_fork_interrupted,
    render_fork_result
)
from ..features.mailbox import render_mailbox_failure
from ..features.processes import monitor_exec_status

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost

RECOVERY_POLL_INTERVAL_SEC: typing.Final[float] = 0.2


class _TurnInterruptNotice:
    """管理单轮中断提示的展示状态。"""

    def __init__(
        self,
        application: ApplicationSink,
        runtime: TuiRuntime,
    ) -> None:
        self._application = application
        self._runtime = runtime

        self.shown: bool = False

    def acknowledge(self) -> None:
        """立即结束 TUI 展示等待，同时保留后台轮次清理屏障。"""
        if self.shown:
            return None
        self.shown = True
        self._runtime.finish_interrupted_presentation()
        emit_tui_interrupt_notice(self._application)


def _restore_recovery_commands(
    host: "TuiApplicationHost",
    runtime: TuiRuntime,
    state: TuiSessionState,
    recovery: SessionRecoveryResult,
) -> None:
    """把确定未执行的持久化命令按普通中断策略恢复到编辑框。"""
    commands = recovery.restore_commands
    if not commands:
        return None

    restored_attachments: list[dict[str, typing.Any]] = []
    restored_extras: dict[str, typing.Any] = {}
    for command in commands:
        restored_attachments.extend(command.attachment_values())
        extras = command.extras_value()
        if extras is not None:
            restored_extras.update(extras)
        runtime.defer_submission(TuiSubmission(
            value=command.message,
            editable_text=command.message,
            paste_store={},
        ))

    current_attachments = tuple(host.attach.pending_attachments_snapshot())
    host.attach.replace_pending_attachments(
        tuple(restored_attachments) + current_attachments
    )
    current_extras = state.consume_pending_prompt_extras()
    restored_extras.update(current_extras)
    if restored_extras:
        state.replace_pending_prompt_extras(restored_extras)
    runtime.restore_interrupted_submissions()


async def _await_durable_session_recovery(
    host: "TuiApplicationHost",
    runtime: TuiRuntime,
    state: TuiSessionState,
    application: ApplicationSink,
    turn_application: TurnApplication["RunResult"],
    protocol_client: ProtocolCommandClient,
    *,
    session_id: str,
) -> bool:
    """等待既有 Durable Turn 权威结算，同时保持输入画布可编辑。"""
    notice_shown = False
    while not host.lifecycle.stop_event.is_set():
        recovery = await turn_application.reconcile_remote_session(
            session_id,
            protocol_client,
        )
        _restore_recovery_commands(host, runtime, state, recovery)
        if not recovery.pending:
            return True
        if not notice_shown:
            emit_tui_recovery_notice(
                application,
                RunRecoveryRequired(recovery.pending),
            )
            notice_shown = True
        try:
            await asyncio.wait_for(
                host.lifecycle.stop_event.wait(),
                timeout=RECOVERY_POLL_INTERVAL_SEC,
            )
        except TimeoutError:
            continue
    return False


def _pending_attachment_snapshot(
    attachment_state: AttachmentStatePort,
) -> tuple[dict[str, typing.Any], ...]:
    """读取并固定待提交附件快照的结构。"""
    return tuple(
        item.copy()
        for item in attachment_state.pending_attachments_snapshot()
    )


async def run_tui_loop(
    host: "TuiApplicationHost",
    *,
    protocol_client: ProtocolCommandClient,
    turn_runner: TuiRootTurnRunner | None = None,
    conversation_compactor: ConversationCompactor | None = None,
    initial_prompt: str | None = None,
    initial_images: tuple[str, ...] = (),
    initial_model: str | None = None,
    turn_application_factory: TurnApplicationFactory | None = None,
) -> None:
    """运行 TUI 会话，并统一关闭其主动 Turn application。"""
    durable_runtime = getattr(host, "application_layout", None) is not None
    if durable_runtime:
        if turn_application_factory is None:
            raise RuntimeError("TUI turn application factory is required")
        turn_application = turn_application_factory(agent_runtime_db_path())
    else:
        turn_application = TurnApplication()
    try:
        if turn_runner is None:
            raise RuntimeError("TUI root turn runner is required")
        await _run_tui_loop(
            host,
            turn_application=turn_application,
            turn_runner=turn_runner,
            conversation_compactor=conversation_compactor,
            protocol_client=protocol_client,
            local_session_id=(
                None
                if durable_runtime
                else f"tui_session_{short_uid(12)}"
            ),
            initial_prompt=initial_prompt,
            initial_images=initial_images,
            initial_model=initial_model,
        )
    finally:
        await turn_application.close(cancel_running=True)


async def _run_tui_loop(
    host: "TuiApplicationHost",
    *,
    turn_application: TurnApplication["RunResult"],
    turn_runner: TuiRootTurnRunner,
    conversation_compactor: ConversationCompactor | None,
    protocol_client: ProtocolCommandClient,
    local_session_id: str | None,
    initial_prompt: str | None,
    initial_images: tuple[str, ...],
    initial_model: str | None,
) -> None:
    """处理 TUI 输入、命令分派和模型轮次。"""
    application = host.frontend.application
    runtime = require_tui_runtime(host.frontend.runtime)

    attachment_state = host.attach
    runtime.bind_pending_attachment_check(
        attachment_state.has_pending_attachments,
    )
    runtime.start_background_task(
        monitor_exec_status(runtime, host),
        name="process status",
    )

    state = TuiSessionState.create(
        host,
        runtime,
        model_override=initial_model,
    )
    foreground_tasks = TuiForegroundTasks(runtime, host)

    dispatcher = TuiCommandDispatcher(
        host,
        runtime,
        state,
        foreground_tasks,
        protocol_client=protocol_client,
        conversation_compactor=conversation_compactor,
        configuration_service_url=host.configuration_service_url,
    )
    dispatcher.mailbox.bind_listener()
    if initial_prompt is not None:
        runtime.submissions.enqueue_message(initial_prompt)
    attachment_start_pending = initial_prompt is None and bool(initial_images)

    while not host.lifecycle.stop_event.is_set():
        await foreground_tasks.wait()
        if host.lifecycle.stop_event.is_set():
            break

        if local_session_id is None:
            recovery_session = host.conversation.snapshot()
            recovery_ready = await _await_durable_session_recovery(
                host,
                runtime,
                state,
                application,
                turn_application,
                protocol_client,
                session_id=derive_local_session_id(
                    "tui",
                    recovery_session,
                ),
            )
            if not recovery_ready:
                break

        if attachment_start_pending:
            attachment_start_pending = False
            prompt_text = ""
            action = DispatchAction.MODEL_TURN
            runtime.set_turn_start_pending(True)
            try:
                await state.refresh_for_prompt(host)
                state.apply_prompt_context(runtime)
            except BaseException:
                runtime.set_turn_start_pending(False)
                raise
        else:
            prompt_task = asyncio.create_task(
                host.frontend.interaction.read_message(state.prompt_context()),
                name="tui read message",
            )
            try:
                await state.refresh_for_prompt(host)
                state.apply_prompt_context(runtime)
                await prompt_task
            except TuiInterruptRequested:
                host.lifecycle.request_stop(exit_code=130)
                break
            except EOFError:
                host.lifecycle.request_stop()
                break
            except UnicodeDecodeError:
                runtime.set_turn_start_pending(False)
                continue
            except TuiTranscriptBacktrackRequested as requested:
                await _handle_transcript_backtrack(
                    host,
                    runtime,
                    state,
                    foreground_tasks,
                    requested.request,
                    protocol_client=protocol_client,
                )
                continue
            except TuiMailboxRunRequested as requested:
                await _handle_mailbox_run(
                    host,
                    runtime,
                    dispatcher,
                    requested.request,
                )
                continue
            except BaseException:
                runtime.set_turn_start_pending(False)
                raise
            finally:
                if not prompt_task.done():
                    prompt_task.cancel()
                await asyncio.gather(prompt_task, return_exceptions=True)

            prompt_text = prompt_task.result()

            submission = runtime.consume_submission_payload()
            if submission is not None and submission.payload_bound:
                host.attach.replace_pending_attachments(
                    submission.attachments
                )
                state.replace_pending_prompt_extras(submission.extras)

            runtime.begin_command_layout()
            try:
                action = (
                    DispatchAction.MODEL_TURN
                    if not prompt_text.strip() and runtime.has_pending_attachments
                    else await dispatcher.dispatch(prompt_text)
                )
            except BaseException:
                runtime.cancel_command_layout()
                runtime.set_turn_start_pending(False)
                raise
            finally:
                runtime.discard_pending_submission()

        if action is DispatchAction.EXIT:
            runtime.set_turn_start_pending(False)
            runtime.finish_command_layout()
            break
        if action is DispatchAction.HANDLED:
            runtime.set_turn_start_pending(False)
            runtime.finish_command_layout()
            continue

        runtime.cancel_command_layout()
        runtime.set_turn_start_pending(True)

        try:
            await state.refresh_preferences(host, ttl_sec=0.0)
        except BaseException:
            runtime.set_turn_start_pending(False)
            raise
        application.emit(ApplicationView(type="tui.gap"))

        host.workspace_runtime.coding.reset_patch_diff()

        turn_id = short_uid(12)

        turn_input_control = TuiTurnInputControl(
            host,
            runtime,
            state,
            cid="",
            sid="",
            turn_id=turn_id,
            protocol_client=protocol_client,
        )

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

        prompt_extras = state.consume_pending_prompt_extras()

        runtime.bind_turn_payload(
            turn_id,
            extras=prompt_extras,
        )

        def bind_prompt_attachments(
            attachments: list[dict[str, typing.Any]]
        ) -> None:
            """把实际提交附件关联到当前用户轮次。"""
            runtime.bind_turn_payload(
                turn_id,
                attachments=attachments,
            )

        interrupt_notice = _TurnInterruptNotice(application, runtime)

        resolved_local_session_id = local_session_id
        remote_session: dict[str, str] | None = None
        if resolved_local_session_id is None:
            remote_session = host.conversation.snapshot()
            resolved_local_session_id = derive_local_session_id(
                "tui",
                remote_session,
            )
        environment_snapshot = capture_active_turn_environment(host)
        submit_command = SubmitTurnCommand.create(
            session_id=resolved_local_session_id,
            message=prompt_text,
            attachments=attachment_snapshot,
            environment_snapshot=environment_snapshot,
            pref_config=state.pref_config,
            extras=prompt_extras,
            trace_context=(
                {
                    "remote_turn": {
                        "cid": remote_session["cid"],
                        "sid": remote_session["sid"],
                        "turn_id": turn_id,
                    },
                }
                if remote_session is not None
                else {}
            ),
        )

        async def execute_submitted_turn(
            command: SubmitTurnCommand,
        ) -> "RunResult":
            """把 TUI Command 适配到现有根轮次执行能力。"""
            return await run_tui_model_turn(
                turn_runner,
                message_text=command.message,
                pref_config=command.pref_config_value() or {},
                permissions=state.permissions,
                attachments=command.attachment_values(),
                environment_snapshot=command.environment_snapshot_value(),
                turn_id=turn_id,
                prompt_extras=command.extras_value(),
                on_prompt_prepared=bind_prompt_attachments,
                turn_input_control=turn_input_control,
                on_interrupt_acknowledged=interrupt_notice.acknowledge,
            )

        await execute_tui_model_turn(
            application,
            runtime,
            turn_application.submit(
                submit_command,
                execute_submitted_turn,
            ),
            turn_input_control=turn_input_control,
            stream_command_handler=dispatcher.handle_stream_command,
            on_interrupt_requested=interrupt_notice.acknowledge,
            show_interrupt_notice=(
                lambda: (
                    not interrupt_notice.shown
                    and not host.lifecycle.stop_event.is_set()
                )
            ),
        )
        runtime.set_turn_start_pending(False)
        exit_reason = runtime.consume_exit_request()
        if exit_reason is not None:
            if exit_reason == "interrupt":
                host.lifecycle.request_stop(exit_code=130)
            else:
                host.lifecycle.request_stop()
            break


async def _handle_mailbox_run(
    host: "TuiApplicationHost",
    runtime: TuiRuntime,
    dispatcher: TuiCommandDispatcher,
    request: MailboxRunRequest
) -> None:
    """在主 TUI 轮次边界串行执行一条收件箱消息。"""
    prepared = dispatcher.mailbox.prepare_run(request)
    try:
        if prepared is None:
            return None
        turn_id = short_uid(12)
        runtime.append_submitted_query(prepared.prompt, turn_id)

        try:
            await execute_tui_model_turn(
                dispatcher.application,
                runtime,
                prepared.listener.run_message(
                    prepared.message_id,
                    turn_id=turn_id,
                ),
                stream_command_handler=dispatcher.handle_stream_command,
                show_interrupt_notice=(
                    lambda: not host.lifecycle.stop_event.is_set()
                ),
            )
        except Exception as error:
            render_mailbox_failure(
                host,
                "Mailbox run failed",
                error,
            )

    finally:
        dispatcher.mailbox.finish_run(request)


async def _handle_transcript_backtrack(
    host: "TuiApplicationHost",
    runtime: TuiRuntime,
    state: TuiSessionState,
    foreground_tasks: TuiForegroundTasks,
    request: TranscriptBacktrackRequest,
    *,
    protocol_client: ProtocolCommandClient,
) -> None:
    """通过前台屏障执行历史分支并恢复选中的输入。"""
    runtime.replace_input_text(request.prompt)
    host.attach.replace_pending_attachments(request.attachments)
    state.replace_pending_prompt_extras(request.extras)

    foreground_tasks.start(
        "Conversation backtrack",
        lambda: fork_current_conversation(
            host,
            protocol_client,
            before_turn_id=request.turn_id,
            bind_target=False,
            fallback_prompt=ResubmittablePrompt(
                message=request.prompt,
                attachments=request.attachments,
                extras=request.extras,
            ),
        ),
        activity_kind="compact",
        on_succeeded=lambda status: _finish_transcript_backtrack(
            host,
            runtime,
            state,
            request,
            status,
        ),
        on_failed=lambda error: render_fork_failure(host, error),
        on_cancelled=lambda: render_fork_interrupted(host),
    )
    await foreground_tasks.wait()


async def _finish_transcript_backtrack(
    host: "TuiApplicationHost",
    runtime: TuiRuntime,
    state: TuiSessionState,
    request: TranscriptBacktrackRequest,
    status: ForkLiveStatus
) -> None:
    """在远端分支结束后提交本地正文或展示失败。"""
    if status.succeeded:
        prompt = status.prompt

        source_session = status.source_session
        target_session = status.target_session

        if (
            prompt is None
            or source_session is None
            or target_session is None
        ):
            render_fork_failure(
                host,
                RuntimeError("Conversation fork did not return commit metadata."),
            )
            return None

        canonical_request = TranscriptBacktrackRequest(
            turn_id=request.turn_id,
            prompt=prompt.message,
            attachments=prompt.attachments,
            extras=prompt.extras,
        )

        if not runtime.can_apply_transcript_backtrack(canonical_request):
            render_fork_failure(
                host,
                RuntimeError("Selected transcript turn is no longer available."),
            )
            return None

        try:
            bound = await host.conversation.bind(
                target_session[0],
                target_session[1],
                source="tui",
            )
            if bound is None:
                raise RuntimeError(
                    "Conversation fork returned invalid session IDs."
                )

            if not runtime.apply_transcript_backtrack(canonical_request):
                raise RuntimeError(
                    "Conversation backtrack could not be committed."
                )

            host.attach.replace_pending_attachments(prompt.attachments)
            state.replace_pending_prompt_extras(prompt.extras)
        except Exception as error:
            try:
                await host.conversation.bind(
                    source_session[0],
                    source_session[1],
                    source="tui:backtrack-rollback",
                )
            except Exception as rollback_error:
                error = RuntimeError(
                    f"{error}; rollback failed: {rollback_error}"
                )
            render_fork_failure(host, error)
            return None

        render_fork_result(host, status)
        return None

    render_fork_result(host, status)


if __name__ == '__main__':
    pass
