# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from agent.ports import (
    ApprovalLedger,
    EffectJournalFactory,
    ModelCapability,
    ProtocolCommandClient,
    PatchPreviewPort,
    RetryStatePort,
    TurnCleanupPort,
    TranscriptFactory,
)
from mind_app.presentation.output import SessionFactory
from agent.application.services import TurnApplicationFactory
from agent.application.turns.run_result import RunResult
from agent.application.turns.commands import (
    SubmitTurnCommand,
    TurnApplication,
)
from agent.application.config.session_identity import derive_local_session_id
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
)
from protocol.schema.identifiers import short_uid
from protocol.client.fork import ResubmittablePrompt
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from ..core.models import (
    MailboxRunRequest,
    TranscriptBacktrackRequest
)
from ..core.submission import (
    TuiInterruptRequested,
    TuiMailboxRunRequested,
    TuiTranscriptBacktrackRequested
)
from ..features.conversation import (
    ForkLiveStatus,
    fork_current_conversation,
    render_fork_failure,
    render_fork_interrupted,
    render_fork_result
)
from ..features.processes import monitor_exec_status
from ..features.mailbox import render_mailbox_failure
from .barriers import TuiForegroundTasks
from .dispatch import (
    DispatchAction,
    TuiCommandDispatcher
)
from .state import TuiSessionState
from .turn import (
    emit_tui_interrupt_notice,
    execute_tui_model_turn,
    run_tui_model_turn
)
from .turn_input import TuiTurnInputControl
from infrastructure.config.runtime_paths import agent_runtime_db_path
from mind_app.interaction.environment import capture_active_turn_environment

if typing.TYPE_CHECKING:
    from ...controller import Mind


@typing.runtime_checkable
class _PendingAttachmentCheck(typing.Protocol):
    """描述会话循环读取待提交附件状态所需的能力。"""

    def has_pending_attachments(self) -> bool:
        """返回当前是否存在待提交附件。"""
        ...


@typing.runtime_checkable
class _PendingAttachmentSnapshot(typing.Protocol):
    """描述会话循环固定待提交附件快照所需的能力。"""

    def pending_attachments_snapshot(
        self,
    ) -> list[dict[str, typing.Any]]:
        """返回待提交附件的独立快照。"""
        ...


class _TurnInterruptNotice:
    """管理单轮中断提示的展示状态。"""

    def __init__(
        self,
        application: ApplicationSink,
        runtime: TuiRuntime,
    ) -> None:
        self._application = application
        self._runtime     = runtime

        self.shown: bool = False

    def acknowledge(self) -> None:
        """立即结束 TUI 展示等待，同时保留后台轮次清理屏障。"""
        if self.shown:
            return None
        self.shown = True
        self._runtime.clear_active_renderable()
        self._runtime.set_execution_active(False)
        emit_tui_interrupt_notice(self._application)


def _pending_attachment_snapshot(
    attachment_state: object
) -> tuple[dict[str, typing.Any], ...]:
    """读取并固定待提交附件快照的结构。"""
    if not isinstance(attachment_state, _PendingAttachmentSnapshot):
        return ()
    return tuple(
        item.copy()
        for item in attachment_state.pending_attachments_snapshot()
    )


async def run_tui_loop(
    mind: "Mind",
    *,
    initial_prompt: str | None = None,
    initial_images: tuple[str, ...] = (),
    initial_model: str | None = None,
    turn_application_factory: TurnApplicationFactory | None = None,
    model_capability: ModelCapability | None = None,
    protocol_client: ProtocolCommandClient | None = None,
    effect_journal_factory: EffectJournalFactory | None = None,
    approval_ledger: ApprovalLedger | None = None,
    session_factory: SessionFactory | None = None,
    transcript_factory: TranscriptFactory | None = None,
    cleanup: TurnCleanupPort | None = None,
    patch_preview: PatchPreviewPort | None = None,
    retry_state: RetryStatePort | None = None,
) -> None:
    """运行 TUI 会话，并统一关闭其主动 Turn application。"""
    durable_runtime = getattr(mind, "application_layout", None) is not None
    if durable_runtime:
        if turn_application_factory is None:
            raise RuntimeError("TUI turn application factory is required")
        turn_application = turn_application_factory(agent_runtime_db_path())
    else:
        turn_application = TurnApplication()
    try:
        await _run_tui_loop(
            mind,
            turn_application=turn_application,
            model_capability=model_capability,
            protocol_client=protocol_client,
            effect_journal_factory=effect_journal_factory,
            approval_ledger=approval_ledger,
            session_factory=session_factory,
            transcript_factory=transcript_factory,
            cleanup=cleanup,
            patch_preview=patch_preview,
            retry_state=retry_state,
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
    mind: "Mind",
    *,
    turn_application: TurnApplication["RunResult"],
    model_capability: ModelCapability | None,
    protocol_client: ProtocolCommandClient | None,
    effect_journal_factory: EffectJournalFactory | None,
    approval_ledger: ApprovalLedger | None,
    session_factory: SessionFactory | None,
    transcript_factory: TranscriptFactory | None,
    cleanup: TurnCleanupPort | None,
    patch_preview: PatchPreviewPort | None,
    retry_state: RetryStatePort | None,
    local_session_id: str | None,
    initial_prompt: str | None,
    initial_images: tuple[str, ...],
    initial_model: str | None,
) -> None:
    """处理 TUI 输入、命令分派和模型轮次。"""
    application = mind.frontend.application
    runtime     = require_tui_runtime(mind.frontend.runtime)

    attachment_state: object = getattr(mind, "attach", None)
    attachment_check = (
        attachment_state.has_pending_attachments
        if isinstance(attachment_state, _PendingAttachmentCheck)
        else None
    )
    runtime.bind_pending_attachment_check(attachment_check)
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
        protocol_client=protocol_client,
    )
    dispatcher.mailbox.bind_listener()
    if initial_prompt is not None:
        runtime.submissions.enqueue_message(initial_prompt)
    attachment_start_pending = initial_prompt is None and bool(initial_images)

    while not mind.task_event.is_set():
        await foreground_tasks.wait()
        if mind.task_event.is_set():
            break

        if attachment_start_pending:
            attachment_start_pending = False
            prompt_text = ""
            action = DispatchAction.MODEL_TURN
            runtime.set_turn_start_pending(True)
            try:
                await state.refresh_for_prompt(mind)
                state.apply_prompt_context(runtime)
            except BaseException:
                runtime.set_turn_start_pending(False)
                raise
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
                runtime.set_turn_start_pending(False)
                continue
            except TuiTranscriptBacktrackRequested as requested:
                await _handle_transcript_backtrack(
                    mind,
                    runtime,
                    state,
                    foreground_tasks,
                    requested.request,
                    protocol_client=protocol_client,
                )
                continue
            except TuiMailboxRunRequested as requested:
                await _handle_mailbox_run(
                    mind,
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
                mind.attach.replace_pending_attachments(
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
            await state.refresh_preferences(mind, ttl_sec=0.0)
        except BaseException:
            runtime.set_turn_start_pending(False)
            raise
        application.emit(ApplicationView(type="tui.gap"))

        mind.workspace_runtime.coding.reset_patch_diff()

        turn_id = short_uid(12)

        turn_input_control = TuiTurnInputControl(
            mind,
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
        if resolved_local_session_id is None:
            resolved_local_session_id = derive_local_session_id(
                "tui",
                mind.conversation.snapshot(),
            )
        environment_snapshot = capture_active_turn_environment(mind)
        submit_command = SubmitTurnCommand.create(
            session_id=resolved_local_session_id,
            message=prompt_text,
            attachments=attachment_snapshot,
            environment_snapshot=environment_snapshot,
            pref_config=state.pref_config,
            extras=prompt_extras,
        )

        async def execute_submitted_turn(
            command: SubmitTurnCommand,
        ) -> "RunResult":
            """把 TUI Command 适配到现有根轮次执行能力。"""
            return await run_tui_model_turn(
                mind,
                message_text=command.message,
                pref_config=command.pref_config_value() or {},
                permissions=state.permissions,
                attachments=command.attachment_values(),
                environment_snapshot=command.environment_snapshot_value(),
                turn_id=turn_id,
                prompt_extras=command.extras_value(),
                model_capability=model_capability,
                protocol_client=protocol_client,
                effect_journal_factory=effect_journal_factory,
                approval_ledger=approval_ledger,
                session_factory=session_factory,
                transcript_factory=transcript_factory,
                cleanup=cleanup,
                patch_preview=patch_preview,
                retry_state=retry_state,
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
            show_interrupt_notice=(
                lambda: (
                    not interrupt_notice.shown
                    and not mind.task_event.is_set()
                )
            ),
        )
        runtime.set_turn_start_pending(False)
        exit_reason = runtime.consume_exit_request()
        if exit_reason is not None:
            if exit_reason == "interrupt":
                mind.exit_code = 130
            mind.task_event.set()
            break


async def _handle_mailbox_run(
    mind: "Mind",
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
                show_interrupt_notice=lambda: not mind.task_event.is_set(),
            )
        except Exception as error:
            render_mailbox_failure(
                mind,
                "Mailbox run failed",
                error,
            )

    finally:
        dispatcher.mailbox.finish_run(request)


async def _handle_transcript_backtrack(
    mind: "Mind",
    runtime: TuiRuntime,
    state: TuiSessionState,
    foreground_tasks: TuiForegroundTasks,
    request: TranscriptBacktrackRequest,
    *,
    protocol_client: ProtocolCommandClient | None = None,
) -> None:
    """通过前台屏障执行历史分支并恢复选中的输入。"""
    runtime.replace_input_text(request.prompt)
    mind.attach.replace_pending_attachments(request.attachments)
    state.replace_pending_prompt_extras(request.extras)

    foreground_tasks.start(
        "Conversation backtrack",
        lambda: fork_current_conversation(
            mind,
            before_turn_id=request.turn_id,
            bind_target=False,
            fallback_prompt=ResubmittablePrompt(
                message=request.prompt,
                attachments=request.attachments,
                extras=request.extras,
            ),
            protocol_client=protocol_client,
        ),
        activity_kind="compact",
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


async def _finish_transcript_backtrack(
    mind: "Mind",
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
                mind,
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
                mind,
                RuntimeError("Selected transcript turn is no longer available."),
            )
            return None

        try:
            bound = await mind.bind_conversation(
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

            mind.attach.replace_pending_attachments(prompt.attachments)
            state.replace_pending_prompt_extras(prompt.extras)
        except Exception as error:
            try:
                await mind.bind_conversation(
                    source_session[0],
                    source_session[1],
                    source="tui:backtrack-rollback",
                )
            except Exception as rollback_error:
                error = RuntimeError(
                    f"{error}; rollback failed: {rollback_error}"
                )
            render_fork_failure(mind, error)
            return None

        render_fork_result(mind, status)
        return None

    render_fork_result(mind, status)


if __name__ == '__main__':
    pass
