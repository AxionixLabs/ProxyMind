# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from collections import deque
from functools import partial
from pathlib import Path
from prompt_toolkit.application import in_terminal
from prompt_toolkit.input.base import Input
from prompt_toolkit.output.base import Output
from mind_core.design.terminal_capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities
)
from mind_core.design.terminal_progress import (
    PassiveTerminalProgress,
    TerminalProgress
)
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.frontend.contracts import (
    ActivityStatusKind,
    FrontendRuntime,
    WaitRetryState
)
from mind_app.interaction.contracts import PromptContext
from mind_app.presentation.terminal_text import sanitize_terminal_line
from ..contracts.resume import (
    ResumePickerRequest,
    ResumePickerResult,
    ResumeRow
)
from .models import (
    CLOSE_MENU_FOOTER_HINT,
    FragmentBlock,
    MailboxEntry,
    MailboxRunRequest,
    MenuAction,
    MenuActionKind,
    MenuRequest,
    TranscriptBacktrackRequest,
    TranscriptExportFormat,
    TranscriptExportResult
)
from .terminal_input import clear_pending_input
from .activity import (
    ActivityLease,
    TuiActivity
)
from .document import (
    SourceBlockRenderer,
    TranscriptCellSource,
    TranscriptBlock,
    TuiBlockKind,
    TuiDocument,
    TuiDocumentState,
    WidthBlockRenderer
)
from .input import TuiInputModel
from .interrupt import TuiExitReason
from .keymap import TuiRuntimeKeymap
from .process_viewer import ProcessViewerRequest
from ..rendering.text_sanitize import sanitize_fragment_block
from .queued import TuiSubmission
from .screen import TuiScreen
from .view import ViewIdentity
from .styles import (
    failure_text_block,
    query_block,
    query_preview_block,
    text_block
)
from ..prompting.commands import (
    resolve_tui_command,
    slash_command_notice_message,
    submission_replaces_query
)
from .submission import (
    TuiInputClosed,
    TuiInterruptRequested,
    TuiMailboxRunRequested,
    TuiTranscriptBacktrackRequested,
    TuiSubmissionFlow
)
from .task_state import TuiTaskState
from .viewport import TuiTranscriptViewport
from ..runtime.background import (
    BackgroundTaskManager,
    DeferredBlock,
    DeferredBlockBuffer,
)
from ..runtime.lifecycle import ApplicationLifecycle
from ..runtime.mailbox import MailboxOverlayCoordinator
from ..runtime.resume_picker import ResumePickerCoordinator
from ..runtime.startup import (
    StartupAnimation,
    StartupFinalFrame,
    StartupPresentationQueue,
)
from ..runtime.state import (
    ActivityHandoffState,
    CommandLayoutState,
    ProcessCompletionStore,
)
from ..runtime.transcript import (
    TranscriptCoordinator,
    TranscriptOverlayCoordinator,
)

ModalResult = typing.TypeVar("ModalResult")


class TuiRuntime(object):
    """协调 TUI Application 生命周期、正文输出和前端交互能力。"""

    def __init__(
        self,
        input_model: TuiInputModel | None = None,
        *,
        input_obj: Input | None = None,
        output_obj: Output | None = None,
        terminal_progress: TerminalProgress | None = None,
        terminal_capabilities: TerminalCapabilities = (
            DEGRADED_TERMINAL_CAPABILITIES
        ),
        keymap: TuiRuntimeKeymap | None = None,
        export_transcript: typing.Callable[
            [typing.Iterable[TranscriptBlock], TranscriptExportFormat],
            TranscriptExportResult,
        ] | None = None
    ) -> None:
        self.input_model = input_model or TuiInputModel()
        self.context     = PromptContext(model="")
        self.keymap      = keymap or TuiRuntimeKeymap.defaults()

        self.terminal_capabilities = terminal_capabilities

        self.task_state = TuiTaskState(
            activity_running=lambda: self.activity.active,
        )
        self.document = TuiDocument()

        self._consumed_submission: TuiSubmission | None = None

        self._background_tasks = BackgroundTaskManager(
            lambda error: self._report_runtime_error(
                "Background task failed",
                error,
            ),
        )
        self._background_blocks = DeferredBlockBuffer()

        self._menu_actions: deque[MenuAction] = deque()
        self._menu_action_scheduled: bool     = False

        self._running_process_status_label: str = ""

        self._process_completions = ProcessCompletionStore()

        self._process_routing_settled: asyncio.Event = asyncio.Event()
        self._process_routing_settled.set()

        self._activity_handoff = ActivityHandoffState()
        self._command_layout = CommandLayoutState()

        self._open_callbacks: list[typing.Callable[[], None]]          = []
        self._turn_finished_callbacks: list[typing.Callable[[], None]] = []
        self._startup_presentations = StartupPresentationQueue()

        self._closing: bool = False

        self._directory_trust_preserved_startup_gate: bool = False

        self._modal_depth: int = 0

        self._turn_progress_active: bool = False

        self.terminal_progress = (
            terminal_progress or PassiveTerminalProgress()
        )

        self.submissions = TuiSubmissionFlow(
            input_model=self.input_model,
            is_submission_deferred=lambda: self.submission_deferred,
            get_input_buffer=lambda: self.screen.input.buffer,
            append_notice=lambda block: self.append_block(
                block,
                kind="notice",
            ),
            invalidate=self.invalidate,
        )

        self.viewport = TuiTranscriptViewport(
            document=self.document,
            is_application_active=lambda: self.active,
            is_scrollback_deferred=self._native_scrollback_deferred,
            is_full_screen_overlay_active=(
                lambda: (
                    self.screen.transcript_overlay.active
                    or self.screen.mailbox_overlay.active
                    or self.screen.resume_picker.active
                )
            ),
            is_closing=lambda: self._closing,
            get_application=lambda: self.screen.application,
            get_terminal_geometry=self._terminal_geometry,
            get_terminal_width=lambda: self.terminal_width,
            get_available_height=(
                lambda: self.screen.transcript_available_height()
            ),
            get_transcript_fragments=(
                lambda: self.screen.transcript_fragments()
            ),
            get_render_info=lambda: self.screen.transcript_window.render_info,
            get_render_revision=lambda: self.screen.application.render_counter,
            get_open_transcript_label=(
                lambda: self.keymap.open_transcript_label
            ),
            clear_terminal_scrollback=(
                lambda: self.screen.clear_terminal_scrollback()
            ),
            clear_terminal_for_resize_replay=(
                lambda: self.screen.clear_terminal_for_resize_replay()
            ),
            begin_synchronized_output=(
                lambda: self.screen.begin_synchronized_output()
            ),
            end_synchronized_output=(
                lambda: self.screen.end_synchronized_output()
            ),
            report_error=self._report_display_error,
            invalidate=self.invalidate,
        )

        self.screen = TuiScreen(
            input_model=self.input_model,
            document=self.document,
            pending_steers=self.submissions.pending_steers,
            queued_messages=self.submissions.queued_messages,
            interrupt_state=self.submissions.interrupt_state,
            get_context=lambda: self.context,
            get_placeholder_text=lambda: self.submissions.placeholder_text,
            get_submission_deferred=lambda: self.submission_deferred,
            get_queued_submission_text=(
                lambda: self.submissions.queued_submission_text
            ),
            get_surface_submission_pending=(
                lambda: self.submissions.surface_submission_pending
            ),
            can_transcript_backtrack=self._can_transcript_backtrack,
            get_transcript_view_row=lambda: self.viewport.view_row,
            accept_input=self.submissions.accept_input,
            on_input_text_changed=self.submissions.on_input_text_changed,
            clear_exit_confirmation=self.submissions.clear_exit_confirmation,
            clear_visible_transcript=self.viewport.clear_visible,
            scroll_transcript_page=self.viewport.scroll_page,
            toggle_transcript_overlay=self.toggle_transcript_overlay,
            close_mailbox_overlay=self.close_mailbox_overlay,
            request_resume_preview=self._request_resume_preview,
            request_resume_transcript=self._request_resume_transcript,
            cancel_resume_preview=self._cancel_resume_preview,
            request_transcript_backtrack=(
                self.submissions.enqueue_transcript_backtrack
            ),
            report_missing_transcript_backtrack=(
                self._report_missing_backtrack
            ),
            export_transcript=export_transcript,
            observe_terminal_geometry=(
                self.viewport.observe_terminal_geometry
            ),
            observe_render_revision=self.viewport.observe_render_revision,
            keymap=self.keymap,
            input_obj=input_obj,
            output_obj=output_obj,
            terminal_capabilities=terminal_capabilities,
        )
        self.input_model.bind_interrupt(self._handle_input_interrupt)

        self.input_model.bind_history_backtrack(
            self._can_backtrack_history,
            self.open_transcript_backtrack,
            self._can_report_missing_backtrack,
            self._report_missing_backtrack,
        )
        self.input_model.bind_input_layout(self.screen.refresh_input_layout)

        self.activity = TuiActivity(
            set_renderable=lambda block: self.screen.set_activity_renderable(
                sanitize_fragment_block(block)
            ),
            clear_renderable=self.screen.clear_activity_renderable,
            get_width=lambda: self.terminal_width,
            color_level=terminal_capabilities.color_level,
        )

        self._application_lifecycle = ApplicationLifecycle(
            self.screen.application,
            clear_pending_input=clear_pending_input,
            finish_input=self.submissions.finish_input,
            reset_synchronized_output=self.screen.reset_synchronized_output,
        )
        self._transcript = TranscriptCoordinator(
            document=self.document,
            viewport=self.viewport,
            overlay=self.screen.transcript_overlay,
            screen=self.screen,
            flush_background_blocks=lambda: self._flush_background_blocks(),
            discard_background_blocks=(
                lambda: self._discard_background_blocks()
            ),
        )
        self._transcript_overlay = TranscriptOverlayCoordinator(
            overlay=self.screen.transcript_overlay,
            viewport=self.viewport,
            screen=self.screen,
            cancel_history_backtrack=(
                lambda: self.input_model.cancel_history_backtrack()
            ),
        )
        self._mailbox_overlay = MailboxOverlayCoordinator(
            overlay=self.screen.mailbox_overlay,
            viewport=self.viewport,
            screen=self.screen,
            cancel_history_backtrack=(
                lambda: self.input_model.cancel_history_backtrack()
            ),
        )
        self._resume_picker = ResumePickerCoordinator(
            viewport=self.viewport,
            screen=self.screen,
            cancel_history_backtrack=(
                lambda: self.input_model.cancel_history_backtrack()
            ),
        )

    @property
    def active(self) -> bool:
        """返回 TUI 应用是否正在运行。"""
        return self._application_lifecycle.active

    @property
    def _application_task(self) -> asyncio.Task[None] | None:
        """返回生命周期协作者当前拥有的任务（迁移期私有观察入口）。"""
        return self._application_lifecycle.task

    @property
    def _application_error(self) -> BaseException | None:
        """返回生命周期协作者记录的错误（迁移期私有观察入口）。"""
        return self._application_lifecycle.error

    @property
    def _application_failure(self) -> asyncio.Event:
        """返回生命周期协作者的失败事件（迁移期私有观察入口）。"""
        return self._application_lifecycle.failure

    @property
    def execution_active(self) -> bool:
        """返回模型轮次是否正在运行。"""
        return self.task_state.turn_running

    @property
    def turn_start_pending(self) -> bool:
        """返回用户输入是否已提交但模型轮次尚未开始。"""
        return self.task_state.turn_start_pending

    @property
    def foreground_active(self) -> bool:
        """返回是否正在等待下一轮开始前的前台屏障。"""
        return self.task_state.foreground_running

    @property
    def submission_deferred(self) -> bool:
        """返回新输入是否需要延迟到下一模型轮次。"""
        return (
            self.task_state.turn_active
            or self.foreground_active
        )

    @property
    def task_running(self) -> bool:
        """返回模型轮次或运行期活动是否正在执行。"""
        return self.task_state.running

    @property
    def terminal_width(self) -> int:
        """返回当前渲染输出的终端列数。"""
        return self.screen.terminal_width

    @property
    def hyperlinks_enabled(self) -> bool:
        """返回动态终端界面是否启用可点击文本链接。"""
        return self.screen.hyperlinks_enabled

    @property
    def terminal_height(self) -> int:
        """返回当前渲染输出的终端行数。"""
        return self.screen.terminal_height

    @property
    def uncertain_steers_active(self) -> bool:
        """返回当前是否存在归属未确认的输入。"""
        return self.submissions.pending_steers.uncertain_active

    @property
    def has_pending_attachments(self) -> bool:
        """返回当前是否存在可随空消息发送的附件。"""
        return self.submissions.has_pending_attachments

    @property
    def inline_process_session_id(self) -> str:
        """返回当前在正文中展示的进程会话标识。"""
        viewer = self.screen.process_viewer
        return viewer.active_session_id if viewer.input_passthrough else ""

    @property
    def command_layout_pending(self) -> bool:
        """返回当前命令结果是否仍等待业务交接完成。"""
        return self._command_layout.pending

    @property
    def directory_trust_active(self) -> bool:
        """返回启动阶段的目录信任界面是否仍在显示。"""
        return self.screen.directory_trust.active

    @property
    def approval_source(self) -> typing.Literal["user"]:
        """声明审批决策来自当前交互用户。"""
        return "user"

    @property
    def startup_gate_active(self) -> bool:
        """返回启动阶段是否仍隐藏主输入画布。"""
        return self.screen.startup_gate_active

    @execution_active.setter
    def execution_active(self, active: bool) -> None:
        """更新模型轮次运行状态。"""
        self.task_state.set_turn_running(active)

    @staticmethod
    def _schedule_menu_action_drain(runtime: "TuiRuntime") -> None:
        """把菜单动作排水回调适配为事件循环参数签名。"""
        runtime._drain_menu_actions()

    def _terminal_geometry(self) -> tuple[int, int]:
        """通过单次尺寸快照返回物理终端宽高。"""
        return self.screen.output_geometry()

    def _request_resume_preview(
        self,
        row: ResumeRow,
        generation: int,
        width: int,
    ) -> None:
        """把 Screen 的 preview 请求转交给已组合的运行时协作者。"""
        coordinator = getattr(self, "_resume_picker", None)
        if coordinator is not None:
            coordinator.request_preview(row, generation=generation, width=width)

    def _request_resume_transcript(
        self,
        row: ResumeRow,
        generation: int,
        width: int,
    ) -> None:
        """把 Screen 的全屏 transcript 请求转交给运行时协作者。"""
        coordinator = getattr(self, "_resume_picker", None)
        if coordinator is not None:
            coordinator.request_transcript(
                row,
                generation=generation,
                width=width,
            )

    def _cancel_resume_preview(self) -> None:
        """取消 picker 当前的 preview 或 transcript 读取任务。"""
        coordinator = getattr(self, "_resume_picker", None)
        if coordinator is not None:
            coordinator.cancel_preview()

    def _native_scrollback_deferred(self) -> bool:
        """判断当前运行状态是否禁止提交原生终端滚屏。"""
        return bool(
            self._modal_depth > 0
            or self.foreground_active
            or self.screen.startup_gate_active
            or self.screen.menu.active
            or self.screen.resume_picker.active
        )

    def _flush_background_blocks(self) -> None:
        """在流式正文结束后提交已完成的后台摘要。"""
        if self.submission_deferred or self.document.active_block is not None:
            return None

        blocks = self._background_blocks.drain()

        with self.screen.visual_update():
            for deferred in blocks:
                self._append_block(
                    deferred.block,
                    kind="notice",
                    transcript_block=deferred.transcript_block,
                    activity_lease=deferred.activity_lease,
                )

    def _discard_background_blocks(self) -> None:
        """丢弃延迟正文并释放其持有的活动区域。"""
        blocks = self._background_blocks.drain()

        with self.screen.visual_update():
            for deferred in blocks:
                if deferred.activity_lease is not None:
                    self.activity.release(deferred.activity_lease)

    def _report_display_error(self, error: BaseException) -> None:
        """把可恢复的终端展示错误追加为稳定提示。"""
        self._report_runtime_error("Display refresh failed", error)

    def _report_runtime_error(
        self,
        label: str,
        error: BaseException
    ) -> None:
        """把可恢复的运行期错误追加为稳定提示。"""
        if self._closing:
            return None

        error_type = type(error).__name__
        detail     = sanitize_terminal_line(str(error))

        description = error_type if not detail else f"{error_type}: {detail}"
        self.queue_background_block(failure_text_block(
            f"{label}: {description}"[:280]
        ))

    def _complete_command_layout(self) -> bool:
        """消费命令结果交接标记并报告是否发生状态变化。"""
        return self._command_layout.consume()

    def _consume_activity_handoff(
        self,
        *,
        deferred: bool
    ) -> ActivityLease | None:
        """消费当前任务等待交接的活动租约。"""
        return self._activity_handoff.consume(
            deferred=deferred,
            freeze=self._freeze_activity_lease,
        )

    def _freeze_activity_lease(self, lease: ActivityLease) -> None:
        """冻结活动租约并隐藏底层布尔返回值。"""
        self.activity.freeze(lease)

    def _can_backtrack_history(self) -> bool:
        """返回主输入区是否可以开始历史编辑选择。"""
        return bool(
            self.active
            and not self.submission_deferred
            and not self.screen.transcript_overlay.active
            and not self.screen.mailbox_overlay.active
            and not self.input_model.shell_mode
            and not self.screen.input.buffer.text
            and not self.has_pending_attachments
            and self.screen.transcript_overlay.has_backtrack_target
        )

    def _can_transcript_backtrack(self) -> bool:
        """返回完整记录是否可以确认历史编辑。"""
        return not self.submission_deferred and not self.has_pending_attachments

    def _can_report_missing_backtrack(self) -> bool:
        """返回主输入区是否可以报告缺少历史编辑目标。"""
        return bool(
            self.active
            and not self.submission_deferred
            and not self.screen.transcript_overlay.active
            and not self.screen.mailbox_overlay.active
            and not self.input_model.shell_mode
            and not self.screen.input.buffer.text
            and not self.has_pending_attachments
            and not self.screen.transcript_overlay.has_backtrack_target
        )

    def _report_missing_backtrack(self) -> None:
        """追加没有可编辑历史消息的提示。"""
        self.queue_background_block(text_block(
            "No previous message to edit."
        ))

    def _refresh_process_status(self) -> None:
        """按完成通知优先级刷新专用进程状态行。"""
        self.screen.process_status.set_label(
            self._process_completions.latest_label(
                self._running_process_status_label,
            ),
        )

    def _handle_input_interrupt(self) -> None:
        """按当前前台交互状态分派输入中断。"""
        viewer = self.screen.process_viewer
        if viewer.input_passthrough:
            if self.submissions.discard_input_draft():
                return None
            viewer.resolve("interrupt")
            self.submissions.interrupt_input()
            return None
        self.submissions.interrupt_input()

    def _drain_menu_actions(self) -> None:
        """执行当前批次菜单动作并隔离同步异常。"""
        self._menu_action_scheduled = False
        if self._closing:
            self._menu_actions.clear()
            return None
        while self._menu_actions:
            action = self._menu_actions.popleft()
            if (
                action.session_id is not None
                and not self.screen.menu.session_is_active(action.session_id)
            ):
                continue
            try:
                action.callback()
            except Exception as error:
                self._show_menu_action_failure(action, error)

    def _settle_startup_presentation(self) -> None:
        """跳过动画并提交当前注册的启动最终帧。"""
        presentations = self._startup_presentations.take()
        for presentation in presentations:
            if presentation.final_frame is not None:
                presentation.final_frame()

    def _show_menu_action_failure(
        self,
        action: MenuAction,
        error: BaseException
    ) -> None:
        """把当前会话中的同步菜单异常转换为失败子面板。"""
        if (
            action.session_id is None
            or not self.screen.menu.session_is_active(action.session_id)
        ):
            self._report_runtime_error(action.name, error)
            return None

        error_type  = type(error).__name__
        detail      = sanitize_terminal_line(str(error))
        description = error_type if not detail else f"{error_type}: {detail}"

        title = (
            "Menu navigation failed"
            if action.kind is MenuActionKind.NAVIGATION
            else "Menu action failed"
        )

        self.push_menu(MenuRequest(
            title=title,
            view_id=f"menu:failure:{action.kind.value}",
            status=sanitize_terminal_line(action.name),
            body=(f"Failed: {description}"[:280],),
            help_text="",
            footer_hint=CLOSE_MENU_FOOTER_HINT,
        ))

    def _append_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind,
        transcript_block: FragmentBlock | None = None,
        source: TranscriptCellSource | None = None,
        raw_text: str | None = None,
        stream_continuation: bool = False,
        display_renderer: WidthBlockRenderer | None = None,
        display_render_width: int | None = None,
        activity_lease: ActivityLease | None = None
    ) -> None:
        """在当前视觉事务中追加正文并完成相关状态交接。"""
        appended = self.document.append_block(
            block,
            kind=kind,
            transcript_block=transcript_block,
            source=source,
            raw_text=raw_text,
            stream_continuation=stream_continuation,
            display_renderer=display_renderer,
            display_render_width=display_render_width,
        )

        if activity_lease is not None:
            self.activity.release(activity_lease)

        if appended:
            self._complete_command_layout()
            self.screen.transcript_overlay.content_changed()
            self.viewport.content_appended()

    def configure_keymap(self, keymap: TuiRuntimeKeymap) -> None:
        """在 Application 启动前替换运行时按键映射。"""
        if self.active:
            raise RuntimeError("cannot configure TUI keymap while running")
        self.screen.set_keymap(keymap)
        self.keymap = keymap

    def configure_scrollback_reflow_line_limit(self, value: int) -> None:
        """配置会话恢复和终端尺寸变化允许回放的最大逻辑行数。"""
        if self.active:
            raise RuntimeError(
                "cannot configure scrollback reflow while TUI is running"
            )
        self.viewport.configure_scrollback_reflow_line_limit(value)

    def set_startup_animation(
        self,
        animation: StartupAnimation,
        *,
        final_frame: StartupFinalFrame | None = None,
    ) -> None:
        """注册在 Application 首帧后播放的一次性启动动画。"""
        if self.active:
            raise RuntimeError("TUI startup animation requires an inactive runtime")
        self._startup_presentations.register(
            animation,
            final_frame=final_frame,
        )

    def bind_pending_attachment_check(
        self,
        check: typing.Callable[[], bool] | None
    ) -> None:
        """绑定或清除待发送附件状态判断。"""
        self.submissions.bind_pending_attachment_check(check)

    def print_exit_summary(self, session_id: str) -> None:
        """在 TUI 释放终端后打印会话恢复提示。"""
        if self.active:
            raise RuntimeError("TUI exit summary requires a closed Application")
        self.screen.print_exit_summary(session_id)

    def add_open_callback(self, callback: typing.Callable[[], None]) -> None:
        """注册主应用首帧完成后的同步回调。"""
        self._open_callbacks.append(callback)
        if self.active:
            callback()

    def add_turn_finished_callback(
        self,
        callback: typing.Callable[[], None],
    ) -> None:
        """注册模型轮次收束事务内执行的同步回调。"""
        self._turn_finished_callbacks.append(callback)

    def set_prompt_context(self, context: PromptContext) -> None:
        """在首帧或输入轮次前更新输入区展示上下文。"""
        self.context = context
        workspace_title = context.workspace_label.replace("\\", "/").rstrip("/")
        if workspace_title == "?":
            workspace_title = ""
        elif "/" in workspace_title:
            workspace_title = workspace_title.rsplit("/", 1)[-1]
        self.terminal_progress.set_workspace_title(workspace_title)

    def set_process_status_label(self, label: str) -> None:
        """更新动画区域下方的后台进程摘要。"""
        self._running_process_status_label = str(label or "")
        self._refresh_process_status()

    def retain_process_completion(
        self,
        snapshot: dict[str, typing.Any],
        *,
        label: str
    ) -> None:
        """保存一项需要通过进程面板确认的完成快照。"""
        if self._process_completions.retain(
            snapshot,
            label=label,
        ):
            self._refresh_process_status()

    def process_completion_snapshots(self) -> tuple[dict[str, typing.Any], ...]:
        """返回尚未确认的后台进程完成快照。"""
        return self._process_completions.snapshots()

    def acknowledge_process_completion(self, session_id: typing.Any) -> None:
        """移除一项已经查看的后台进程完成状态。"""
        if self._process_completions.acknowledge(session_id):
            self._refresh_process_status()

    def begin_terminal_progress(self) -> None:
        """启动终端窗口的不确定进度。"""
        self._turn_progress_active = True
        self.terminal_progress.begin()

    def end_terminal_progress(self) -> None:
        """清除终端窗口进度。"""
        self._turn_progress_active = False
        self.terminal_progress.clear()

    def update_menu(self, request: MenuRequest) -> None:
        """更新主画布中的菜单或只读面板。"""
        self.screen.menu.update(request)

    def finish_menu(self, value: typing.Any = None) -> None:
        """结束主画布中的菜单或只读面板。"""
        self.screen.menu.finish(value)

    def cancel_menu(self) -> None:
        """取消主画布中的当前菜单或只读面板。"""
        self.screen.menu.cancel()

    def push_menu(self, request: MenuRequest) -> None:
        """在当前菜单上压入子菜单并保留父级状态。"""
        self.discard_pending_submission()
        self.screen.menu.push(request)

    def emit_menu_action(
        self,
        action: typing.Callable[[], None],
        *,
        name: str = "tui menu action",
        kind: MenuActionKind = MenuActionKind.DOMAIN
    ) -> bool:
        """把菜单导航或业务动作排到下一次事件循环。"""
        if self._closing:
            return False
        self._menu_actions.append(MenuAction(
            action,
            name=name,
            session_id=self.screen.menu.active_session_id,
            kind=kind,
        ))
        if not self._menu_action_scheduled:
            self._menu_action_scheduled = True
            asyncio.get_running_loop().call_soon(
                TuiRuntime._schedule_menu_action_drain,
                self,
            )
        return True

    def replace_active_menu_if_id(
        self,
        view_id: str,
        request: MenuRequest,
        *,
        session_id: int | None = None,
    ) -> bool:
        """仅在栈顶菜单标识匹配时刷新内容。"""
        return self.screen.menu.replace_active_if_id(
            view_id,
            request,
            session_id=session_id,
        )

    def replace_present_menu_if_id(
        self,
        view_id: str,
        request: MenuRequest,
        *,
        session_id: int | None = None,
    ) -> bool:
        """刷新栈中仍存在的指定菜单。"""
        return self.screen.menu.replace_present_if_id(
            view_id,
            request,
            session_id=session_id,
        )

    def replace_present_menus_if_id(
        self,
        updates: typing.Iterable[tuple[str, MenuRequest]],
        *,
        session_id: int | None = None,
    ) -> int:
        """在同一会话内原子刷新多个仍存在的菜单 view。"""
        return self.screen.menu.replace_present_many_if_id(
            updates,
            session_id=session_id,
        )

    def dismiss_menu_by_id(
        self,
        view_id: str,
        *,
        session_id: int | None = None,
    ) -> bool:
        """按标识取消菜单及其上方子菜单。"""
        return self.screen.menu.dismiss_view_by_id(
            view_id,
            session_id=session_id,
        )

    def dismiss_menus_by_id(
        self,
        view_ids: typing.Iterable[str],
        *,
        session_id: int | None = None,
    ) -> int:
        """从最浅命中标识开始取消多个菜单层。"""
        return self.screen.menu.dismiss_views_by_id(
            view_ids,
            session_id=session_id,
        )

    def active_menu_session_id(self) -> int | None:
        """返回当前菜单会话标识，供领域 action 绑定生命周期。"""
        return self.screen.menu.active_session_id

    def active_menu_view_identity(self) -> ViewIdentity | None:
        """返回当前菜单 view 的稳定身份快照。"""
        return self.screen.menu.active_view_identity()

    def menu_session_is_active(self, session_id: int) -> bool:
        """判断菜单会话是否仍可接受异步结果。"""
        return self.screen.menu.session_is_active(session_id)

    def show_directory_trust_error(self, message: str) -> None:
        """显示目录信任状态保存失败信息。"""
        self.screen.directory_trust.show_error(message)

    def begin_process_viewer(
        self,
        request: ProcessViewerRequest,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        gap_before: int | None = None
    ) -> asyncio.Future[typing.Any]:
        """同步激活进程查看器并返回等待结果。"""
        self.discard_pending_submission()

        if not request.capture_input:
            self.screen.synchronize_next_render()

        self.set_active_renderable(
            block,
            kind="operation",
            transcript_block=transcript_block,
            gap_before=gap_before,
        )

        return self.screen.process_viewer.begin(request)

    def update_process_viewer(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        gap_before: int | None = None
    ) -> None:
        """替换当前动态进程正文。"""
        if self.screen.process_viewer.input_passthrough:
            self.screen.synchronize_next_render()

        self.set_active_renderable(
            block,
            kind="operation",
            transcript_block=transcript_block,
            gap_before=gap_before,
        )

    def resolve_process_viewer(self, value: typing.Any = None) -> None:
        """提交当前进程查看动作并解除等待。"""
        self.screen.process_viewer.resolve(value)

    def commit_process_viewer(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """原位提交进程摘要并恢复主输入区域。"""
        if self.document.active_kind != "operation":
            raise RuntimeError("cannot commit a process without active output")

        input_passthrough = self.screen.process_viewer.input_passthrough
        if input_passthrough:
            self.screen.synchronize_next_render()

        self.document.commit_active(block, transcript_block=transcript_block)
        self.screen.process_viewer.settle()
        self.screen.transcript_overlay.content_changed()
        self.viewport.stable_content_changed()
        self._flush_background_blocks()

    def dismiss_process_viewer(self) -> None:
        """撤下动态进程正文并恢复主输入区域。"""
        changed = self.document.active_kind == "operation"

        input_passthrough = self.screen.process_viewer.input_passthrough
        if input_passthrough:
            self.screen.synchronize_next_render()
        if changed:
            self.document.clear_active()

        self.screen.process_viewer.settle()
        if changed:
            self.screen.transcript_overlay.content_changed()
            self.viewport.stable_content_changed()
            self._flush_background_blocks()

    def commit_process_result(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """用稳定进程摘要替换刚提交的命令输入。"""
        self.discard_pending_submission()

        self.document.set_active(
            block,
            kind="operation",
            transcript_block=transcript_block,
        )

        self.document.commit_active(block, transcript_block=transcript_block)
        self.screen.transcript_overlay.content_changed()
        self.viewport.stable_content_changed()
        self._flush_background_blocks()

    def start_background_task(
        self,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
        *,
        name: str
    ) -> asyncio.Task[None]:
        """启动由 TUI 生命周期管理的后台任务。"""
        return self._background_tasks.start(coroutine, name=name)

    def start_background_session_task(
        self,
        session_id: str,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
    ) -> None:
        """为指定进程会话启动唯一的后台监视任务。"""
        self._background_tasks.start_session(session_id, coroutine)

    def cancel_background_session_task(self, session_id: str) -> None:
        """取消指定进程会话的后台监视任务。"""
        self._background_tasks.cancel_session(session_id)

    def queue_background_block(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """在不打断流式正文的边界提交后台摘要。"""
        transcript_block = transcript_block or block
        if (
            self.execution_active
            or self.document.active_block is not None
            or self.screen.menu.active
        ):
            lease = self._consume_activity_handoff(deferred=True)
            self._background_blocks.append(DeferredBlock(
                block=block,
                transcript_block=transcript_block,
                activity_lease=lease,
            ))
            return None

        self.append_block(
            block,
            kind="notice",
            transcript_block=transcript_block,
        )

    def append_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind = "system",
        transcript_block: FragmentBlock | None = None,
        source: TranscriptCellSource | None = None,
        raw_text: str | None = None,
        stream_continuation: bool = False,
        display_renderer: WidthBlockRenderer | None = None,
        display_render_width: int | None = None
    ) -> None:
        """向会话内容追加一个稳定展示块。"""
        with self.screen.visual_update():
            self._append_block(
                block,
                kind=kind,
                transcript_block=transcript_block,
                source=source,
                raw_text=raw_text,
                stream_continuation=stream_continuation,
                display_renderer=display_renderer,
                display_render_width=display_render_width,
                activity_lease=self._consume_activity_handoff(
                    deferred=False,
                ),
            )

    def replace_transcript(self, blocks: typing.Iterable[TranscriptBlock]) -> None:
        """用恢复内容替换当前记录并重置终端视口。"""
        self._transcript.replace(blocks)

    def discard_pending_submission(self) -> None:
        """清理由命令分派结束后仍未接管的暂存输入。"""
        if self.document.discard_submission():
            self.invalidate()

    def begin_command_layout(self) -> None:
        """标记命令结果开始接管当前活动区域。"""
        self._command_layout.begin()
        self._process_routing_settled.clear()

    def cancel_command_layout(self) -> None:
        """取消当前命令结果的业务交接标记。"""
        self._command_layout.cancel()
        self._process_routing_settled.set()

    def finish_command_layout(self, *, force: bool = False) -> None:
        """结束命令结果交接并请求一次当前帧重绘。"""
        with self.screen.visual_update():
            completed = self._complete_command_layout()
            if force and not completed:
                completed = True
            if completed:
                self.invalidate()
        self.cancel_command_layout()

    def activity_handoff(
        self,
        kind: ActivityStatusKind | None,
    ) -> contextlib.AbstractContextManager[None]:
        """让当前任务的首个可见结果接管指定活动区域。"""

        @contextlib.contextmanager
        def transaction() -> typing.Iterator[None]:
            lease = self.activity.lease(kind) if kind is not None else None
            deferred = bool(
                self.execution_active
                or self.document.active_block is not None
            )

            if lease is not None and deferred:
                with self.screen.visual_update():
                    self.activity.freeze(lease)

            with self._activity_handoff.bind(
                lease,
                deferred=deferred,
            ) as handoff:
                try:
                    yield
                finally:
                    if lease is not None and not handoff.consumed:
                        with self.screen.visual_update():
                            self.activity.release(lease)

        return transaction()

    def replace_input_text(
        self,
        text: str,
        *,
        selected_skill: bool = False
    ) -> None:
        """替换主输入内容并把光标移动到末尾。"""
        value = str(text)

        buffer = self.screen.input.buffer
        self.input_model.clear_selected_skill()
        buffer.text = value
        buffer.cursor_position = len(value)

        if selected_skill:
            self.input_model.confirm_selected_skill(buffer)

        self.input_model.notify_input_layout()
        self.invalidate()

    def bind_submitted_turn(
        self,
        turn_id: str,
        prompt: str,
        *,
        has_attachments: bool = False,
        attachment_labels: typing.Iterable[str] = ()
    ) -> bool:
        """把最近提交的用户正文关联到即将执行的模型轮次。"""
        changed = self.document.bind_latest_user_turn(
            turn_id,
            prompt,
        )

        if not changed and has_attachments:
            labels = [
                label
                for value in attachment_labels
                if (label := str(value or "").strip())
            ]

            summary = ", ".join(labels) or "attachment"

            self.append_block(
                query_block(f"[Attachment: {summary}]"),
                kind="user",
            )

            changed = self.document.bind_latest_user_turn(
                turn_id,
                prompt,
            )

        if changed:
            self.screen.transcript_overlay.content_changed()
        return changed

    def append_submitted_query(
        self,
        prompt: str,
        turn_id: str,
        *,
        max_display_rows: int = 8
    ) -> bool:
        """把已通过命令分派的用户输入作为单次视觉事务提交。"""
        value = str(prompt)
        if not value.strip():
            return False

        width      = self.terminal_width
        transcript = query_block(value, command_aware=False)

        renderer = partial(
            query_preview_block,
            value,
            transcript_key=self.keymap.open_transcript_label,
            max_rows=max_display_rows,
        )
        display = renderer(width)

        with self.screen.visual_update():
            self.viewport.reset_view()
            self._append_block(
                display,
                kind="user",
                transcript_block=transcript,
                raw_text=value,
                display_renderer=renderer,
                display_render_width=width,
            )
            if not self.document.bind_latest_user_turn(turn_id, value):
                raise RuntimeError("submitted query could not be bound")
            self.screen.synchronize_next_render()
            self.screen.transcript_overlay.content_changed()

        return True

    def append_turn_input(
        self,
        turn_id: str,
        submission: TuiSubmission
    ) -> bool:
        """把采样边界接纳的用户输入追加到当前逻辑轮次。"""
        visible = submission.visible_text.strip()
        if not visible and submission.attachments:
            labels = [
                label
                for item in submission.attachments
                if (
                    label := str(
                        item.get("filename") or item.get("name") or ""
                    ).strip()
                )
            ]
            visible = f"[Attachment: {', '.join(labels) or 'attachment'}]"

        if not visible:
            return False

        self.append_block(query_block(visible), kind="user")

        if not self.document.bind_latest_user_turn(turn_id, submission.value):
            raise RuntimeError("accepted turn input could not be bound")

        if not self.bind_turn_payload(
            turn_id,
            attachments=submission.attachments,
            extras=submission.extras,
        ):
            raise RuntimeError("accepted turn input payload could not be bound")

        return True

    def apply_transcript_backtrack(
        self,
        request: TranscriptBacktrackRequest
    ) -> bool:
        """截断已分叉的本地正文并恢复选中的用户输入。"""
        document_state: TuiDocumentState = self.document.capture_state()

        buffer       = self.screen.input.buffer
        input_text   = buffer.text
        input_cursor = buffer.cursor_position
        view_row     = self.viewport.view_row

        try:
            if not self.document.truncate_before_turn(request.turn_id):
                return False

            self.replace_input_text(request.prompt)
            self.viewport.reset_view()
            self.screen.transcript_overlay.content_changed()
            self.screen.clear_terminal_scrollback()
            self.viewport.stable_content_changed()
            self.viewport.clear_restored_history_notice()
            return True

        except BaseException:
            self.viewport.pause_scrollback()
            self.document.restore_state(document_state)

            buffer.text = input_text
            buffer.cursor_position = min(input_cursor, len(input_text))

            self.viewport.view_row = view_row

            with contextlib.suppress(Exception):
                self.screen.transcript_overlay.content_changed()

            self.invalidate()
            raise

    def can_apply_transcript_backtrack(
        self,
        request: TranscriptBacktrackRequest
    ) -> bool:
        """判断历史编辑请求是否可在当前稳定正文上提交。"""
        return self.document.can_truncate_before_turn(request.turn_id)

    def bind_turn_payload(
        self,
        turn_id: str,
        *,
        attachments: typing.Iterable[typing.Mapping[str, typing.Any]] | None = None,
        extras: typing.Mapping[str, typing.Any] | None = None
    ) -> bool:
        """把实际模型请求载荷关联到已绑定的用户轮次。"""
        changed = self.document.bind_turn_payload(
            turn_id,
            attachments=attachments,
            extras=extras,
        )

        if changed:
            self.screen.transcript_overlay.content_changed()
        return changed

    def set_active_renderable(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind = "assistant",
        transcript_block: FragmentBlock | None = None,
        raw_text: str | None = None,
        stream_continuation: bool = False,
        gap_before: int | None = None
    ) -> None:
        """替换当前流式展示块。"""
        self._transcript.set_active(
            block,
            kind=kind,
            transcript_block=transcript_block,
            raw_text=raw_text,
            stream_continuation=stream_continuation,
            gap_before=gap_before,
        )

    def invalidate(self) -> None:
        """请求重新绘制当前稳定画布。"""
        self.screen.invalidate()

    def toggle_transcript_overlay(self) -> None:
        """切换完整会话记录并协调原生滚屏任务。"""
        self._transcript_overlay.toggle()

    def set_mailbox_entries(
        self,
        entries: typing.Iterable[MailboxEntry],
        *,
        listener_active: bool
    ) -> None:
        """把远端请求快照同步到静态收件箱画面。"""
        self._mailbox_overlay.update(
            entries,
            listener_active=listener_active,
        )

    def mailbox_entries(self) -> tuple[MailboxEntry, ...]:
        """返回已经过终端文本过滤的收件箱展示快照。"""
        return self._mailbox_overlay.entries

    def enqueue_mailbox_run(
        self,
        message_id: str,
        *,
        automatic: bool
    ) -> None:
        """把一条收件箱执行请求投递到 TUI 主输入事件队列。"""
        self.submissions.message_queue.put_nowait(MailboxRunRequest(
            message_id=str(message_id),
            automatic=automatic,
        ))

    def close_mailbox_overlay(self) -> None:
        """关闭全屏消息详情并恢复等待中的菜单流程。"""
        self._mailbox_overlay.close()

    def open_transcript_backtrack(self) -> None:
        """从主输入区打开完整记录并选择最近用户轮次。"""
        self._transcript_overlay.open_backtrack()

    def commit_active_renderable(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        raw_text: str | None = None,
        source_renderer: SourceBlockRenderer | None = None,
        source_render_width: int | None = None
    ) -> None:
        """把当前动态正文替换为同位置的稳定块。"""
        self._transcript.commit_active(
            block,
            transcript_block=transcript_block,
            raw_text=raw_text,
            source_renderer=source_renderer,
            source_render_width=source_render_width,
        )

    def commit_active_stream_prefix(
        self,
        block: FragmentBlock,
        *,
        raw_text: str,
        source_renderer: SourceBlockRenderer | None = None,
        source_render_width: int | None = None
    ) -> None:
        """提交流式正文的稳定前缀并继续保留当前执行周期。"""
        self._transcript.commit_stream_prefix(
            block,
            raw_text=raw_text,
            source_renderer=source_renderer,
            source_render_width=source_render_width,
        )

    def clear_active_renderable(self) -> None:
        """清空当前流式展示块。"""
        self._transcript.clear_active()

    def set_execution_active(self, active: bool) -> None:
        """更新模型轮次执行状态并切换输入区布局。"""
        was_active = (
            self.execution_active
            or self.task_state.turn_finishing
        )
        active     = bool(active)

        with self.screen.visual_update():
            self.execution_active = active
            if not self.execution_active:
                if was_active:
                    for callback in tuple(self._turn_finished_callbacks):
                        callback()
                self.submissions.clear_queued_submission_marker()
                self._flush_background_blocks()

            self.invalidate()

        if not self.execution_active:
            self.viewport.schedule_scrollback_flush()

    def set_turn_start_pending(self, pending: bool) -> None:
        """更新已提交但尚未开始的模型轮次状态。"""
        self.task_state.set_turn_start_pending(pending)

    def finish_turn_wait(self) -> None:
        """结束模型轮次等待状态的生命周期所有权。"""
        self.task_state.finish_turn_wait()

    def set_foreground_active(self, active: bool) -> None:
        """更新下一轮开始前的前台屏障状态。"""
        self.task_state.set_foreground_running(active)

        if not self.submission_deferred:
            self.submissions.clear_queued_submission_marker()
            self._flush_background_blocks()
            self.viewport.schedule_scrollback_flush()

        self.invalidate()

    def set_wait_retry_state(self, state: WaitRetryState) -> None:
        """切换等待动画的重试来源并保持当前动画相位。"""
        self.activity.set_wait_retry_state(state)

    def bind_interrupt_handler(
        self,
        handler: typing.Callable[[], bool] | None
    ) -> None:
        """绑定或清除当前可中断生命周期的取消函数。"""
        self.submissions.bind_interrupt_handler(handler)

    def bind_stream_command_handler(
        self,
        handler: typing.Callable[[str], bool] | None
    ) -> None:
        """绑定或清除忙碌期间的命令分派函数。"""
        self.submissions.bind_stream_command_handler(handler)

    def bind_turn_input_handler(
        self,
        handler: typing.Callable[[TuiSubmission, bool], bool] | None
    ) -> None:
        """绑定或清除活动模型轮次的输入接管函数。"""
        self.submissions.bind_turn_input_handler(handler)

    def bind_queued_restore_handler(
        self,
        handler: typing.Callable[[TuiSubmission], None] | None
    ) -> None:
        """绑定或清除取回队列消息时的结构化草稿恢复。"""
        self.submissions.bind_queued_restore_handler(handler)

    def defer_submission(
        self,
        submission: TuiSubmission
    ) -> None:
        """把用户主动排队的结构化输入保留到后续轮次。"""
        self.submissions.defer_submission(submission)

    def defer_rejected_steer(self, submission: TuiSubmission) -> None:
        """把未消费的即时输入保留到下一轮优先重试。"""
        self.submissions.defer_rejected_steer(submission)

    def discard_rejected_steer(
        self,
        client_message_id: str
    ) -> TuiSubmission | None:
        """移除已经由当前轮次确认消费的即时输入重试项。"""
        return self.submissions.discard_rejected_steer(client_message_id)

    def track_pending_steer(self, submission: TuiSubmission) -> None:
        """展示一条等待当前轮次接收的输入。"""
        self.submissions.track_pending_steer(submission)

    def resolve_pending_steer(self, client_message_id: str) -> None:
        """停止展示一条已经完成归属转换的输入。"""
        self.submissions.resolve_pending_steer(client_message_id)

    def retain_uncertain_steer(self, submission: TuiSubmission) -> None:
        """保留一条不得自动重试的未确认输入。"""
        self.submissions.retain_uncertain_steer(submission)

    def consume_submission_payload(self) -> TuiSubmission | None:
        """读取最近一项输入携带的附件和扩展字段。"""
        submission = self._consumed_submission
        self._consumed_submission = None
        return submission

    def request_turn_interrupt(self) -> None:
        """把当前轮次标记为用户主动中断。"""
        self.submissions.request_turn_interrupt()

    def consume_turn_interrupt(self) -> bool:
        """消费并返回当前轮次是否由用户主动中断。"""
        return self.submissions.consume_turn_interrupt()

    def consume_exit_request(self) -> TuiExitReason | None:
        """消费并返回主输入区是否已请求退出。"""
        return self.submissions.consume_exit_request()

    def begin_startup_gate(self) -> None:
        """激活启动阶段独占画布并阻止主输入。"""
        self.screen.set_startup_gate(True)

    async def _exit_application(self, *, erase: bool) -> None:
        """结束当前应用任务并按需清除画布。"""
        await self._application_lifecycle.stop(erase=erase)

    async def _play_startup_animation(self) -> None:
        """播放并清除当前注册的启动动画。"""
        presentations = self._startup_presentations.take()
        for presentation in presentations:
            await presentation.animation()

    async def _finish_approval_session(self, wait_paused: bool) -> None:
        """恢复等待状态并关闭当前审批卡。"""
        if self._turn_progress_active:
            self.terminal_progress.begin()
        else:
            self.terminal_progress.clear()
        try:
            if wait_paused:
                await self.activity.resume_wait()
        finally:
            await self.screen.approval.dismiss()

    async def open(self) -> None:
        """启动持久 inline 输入应用并等待首帧完成。"""
        if self.active:
            return None

        self._closing = False
        self.terminal_progress.clear()

        self._application_lifecycle.reset_for_open()

        self.screen.set_transcript_only(False)

        application = self.screen.application

        previous_render_count = application.render_counter

        application_task = self._application_lifecycle.start()

        while (
            (
                not application.is_running
                or application.render_counter == previous_render_count
            )
            and not application_task.done()
        ):
            await asyncio.sleep(0)

        application_error = self._application_lifecycle.exception()
        if application_error is not None:
            raise application_error

        if not (
            self.screen.directory_trust.active
            or self.screen.startup_gate_active
        ):
            await self._play_startup_animation()

        self.viewport.refresh_geometry()

        for callback in tuple(self._open_callbacks):
            callback()

    async def close(self) -> None:
        """停止输入应用和全部动态任务。"""
        self._closing = True
        self._application_lifecycle.mark_closing()

        self._menu_actions.clear()

        self._menu_action_scheduled = False
        self._turn_progress_active  = False

        preserve_transcript = self.document.has_conversation

        self._startup_presentations.clear()
        self.terminal_progress.close()

        await self._resume_picker.close()
        await self.submissions.close()
        await self.activity.clear()

        self.task_state.clear()
        self._running_process_status_label = ""
        self._process_completions.clear()
        self.screen.process_status.clear()

        await self.screen.approval.close()
        await self.screen.menu.close()
        # Future.set_result 会在当前事件循环的下一次调度中恢复等待方。
        # 关闭协议返回前让这些调用方完成 finally，避免留下悬挂的菜单协程。
        await asyncio.sleep(0)
        await self.screen.process_viewer.close()

        if self.document.active_kind == "operation":
            self.document.clear_active()

        self.document.discard_submission()
        self.screen.bottom_pane.clear()

        if self.screen.transcript_overlay.active:
            self.screen.set_transcript_overlay(False)
        if self.screen.mailbox_overlay.active:
            self._mailbox_overlay.close()

        self.screen.set_transcript_only(preserve_transcript)

        await self._background_tasks.close()

        await self.viewport.close()
        await self._exit_application(erase=not preserve_transcript)

        self.screen.directory_trust.close()
        self.screen.set_startup_gate(False)
        self._directory_trust_preserved_startup_gate = False

    async def view_resume_picker(
        self,
        request: ResumePickerRequest,
    ) -> ResumePickerResult:
        """打开全屏 Resume picker 并返回选择或取消。"""
        return await self._resume_picker.view(request)

    async def view_mailbox_entry(
        self,
        entry_key: str,
        *,
        allow_menu: bool = False
    ) -> bool:
        """冻结原生滚屏并等待单条消息详情关闭。"""
        return await self._mailbox_overlay.view_entry(
            entry_key,
            allow_menu=allow_menu,
        )

    async def finish_startup_gate(self) -> None:
        """释放启动阶段独占画布并恢复主输入焦点。"""
        if not self.screen.startup_gate_active:
            return None
        if self.screen.menu.active:
            await self.screen.menu.close()
        self.screen.set_startup_gate(False)
        self.screen.prepare_startup_transition()
        await self._play_startup_animation()
        self.viewport.refresh_geometry()

    async def settle_startup_gate(self) -> None:
        """直接提交启动最终帧并释放独占画布。"""
        if not self.screen.startup_gate_active:
            return None
        if self.screen.menu.active:
            await self.screen.menu.close()
        self._settle_startup_presentation()
        self.screen.set_startup_gate(False)
        self.screen.prepare_startup_transition()

    async def finish_directory_trust(self) -> None:
        """关闭目录信任界面并保留或释放启动输入屏障。"""
        self.screen.directory_trust.close()
        if self._directory_trust_preserved_startup_gate:
            self._directory_trust_preserved_startup_gate = False
            self.viewport.refresh_geometry()
            return None
        await self.finish_startup_gate()

    async def detach_inline_process_viewer(self) -> None:
        """在提交新输入前撤下保持输入可见的进程查看器。"""
        viewer = self.screen.process_viewer
        if not viewer.input_passthrough:
            return None

        viewer.resolve("detach")
        await viewer.wait_settled()

    async def wait_directory_trust(self) -> bool:
        """等待目录信任界面的下一次选择。"""
        return await self.screen.directory_trust.wait() == "trust"

    async def wait_for_process_routing_boundary(self) -> None:
        """等待当前命令和临时交互结束后再决定进程结果落点。"""
        while (
            self.command_layout_pending
            or self.screen.bottom_pane.transient_active
        ):
            self._process_routing_settled.clear()
            await self._process_routing_settled.wait()

    async def wait_for_application_failure(self) -> BaseException:
        """等待输入应用异常停止并返回原始错误。"""
        return await self._application_lifecycle.wait_failure()

    async def begin_directory_trust(
        self,
        cwd: Path,
        trust_target: Path
    ) -> None:
        """在主 Application 中打开启动阶段的目录信任界面。"""
        self._directory_trust_preserved_startup_gate = (
            self.screen.startup_gate_active
        )
        self.begin_startup_gate()
        self.screen.directory_trust.begin(cwd, trust_target)
        try:
            await self.open()
        except BaseException:
            self.screen.directory_trust.close()
            self._directory_trust_preserved_startup_gate = False
            raise

    async def read_message(
        self,
        context: PromptContext
    ) -> str:
        """更新输入上下文并按提交顺序读取下一条消息。"""
        self.set_prompt_context(context)

        try:
            submission = await self.submissions.read_submission()
        except TuiInputClosed:
            application_error = self._application_lifecycle.exception()
            if application_error is not None:
                if isinstance(application_error, KeyboardInterrupt):
                    raise TuiInterruptRequested from application_error
                raise application_error
            raise EOFError

        if isinstance(submission, TranscriptBacktrackRequest):
            raise TuiTranscriptBacktrackRequested(submission)

        if isinstance(submission, MailboxRunRequest):
            raise TuiMailboxRunRequested(submission)

        if isinstance(submission, TuiSubmission):
            self._consumed_submission = submission
            value = submission.value
            visible = submission.visible_text.strip() or value
        else:
            self._consumed_submission = None
            value = str(submission)
            visible = value

        model_submission = self._is_model_submission(value)
        if model_submission:
            self.set_turn_start_pending(True)

        try:
            await self.detach_inline_process_viewer()
        except BaseException:
            if model_submission:
                self.set_turn_start_pending(False)
            raise

        self.viewport.reset_view()

        if (
            visible
            and not submission_replaces_query(value)
            and not slash_command_notice_message(value)
        ):
            block = query_block(visible)
            self.document.stage_submission(block, raw_text=visible)
            if resolve_tui_command(value) is not None:
                self.invalidate()
            else:
                committed = self.document.commit_submission()
                if committed is not None:
                    self.screen.synchronize_next_render()
                    self.screen.transcript_overlay.content_changed()
                    self.viewport.content_appended()

        self.submissions.clear_surface_submission_pending()

        return value

    def _is_model_submission(self, value: str) -> bool:
        """判断提交是否应进入模型轮次等待交接。"""
        normalized = str(value or "").strip()
        if not normalized:
            return self.has_pending_attachments
        return not normalized.startswith(("/", "!")) and normalized not in {
            "$",
            "\\",
        }

    async def select_menu(
        self,
        request: MenuRequest
    ) -> typing.Any:
        """在主 Application 画布内读取菜单选择。"""
        self.discard_pending_submission()
        self._process_routing_settled.clear()
        try:
            return await self.screen.menu.request(request)
        finally:
            self._process_routing_settled.set()
            self.viewport.schedule_scrollback_flush()
            if not self.screen.menu.active:
                self._flush_background_blocks()

    async def request_approval(
        self,
        approval: dict[str, typing.Any]
    ) -> ApprovalDecisionValue:
        """在唯一审批区域中读取工具执行决策。"""
        if not self.screen.approval.begin(approval):
            return "expired"

        wait_paused: bool = False

        self.terminal_progress.warning()

        try:
            wait_paused = await self.activity.pause_wait()
            decision    = await self.screen.approval.wait()
        except BaseException:
            await self._finish_approval_session(wait_paused)
            raise

        await self._finish_approval_session(wait_paused)

        return decision

    async def view_process(
        self,
        request: ProcessViewerRequest,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        ready_event: asyncio.Event | None = None
    ) -> typing.Any:
        """显示动态进程正文并等待查看器动作。"""
        try:
            future = self.begin_process_viewer(
                request,
                block,
                transcript_block=transcript_block,
            )
        except BaseException:
            if ready_event is not None:
                ready_event.set()
            raise

        if ready_event is not None:
            ready_event.set()

        try:
            return await future
        except BaseException:
            self.dismiss_process_viewer()
            raise

    async def begin_wait_status(self) -> None:
        """启动覆盖当前交互周期的等待动画。"""
        if self.task_state.turn_wait_active:
            await self.activity.ensure_wait()
            return None
        await self.activity.begin_wait()

    async def ensure_wait_status_for_turn(self) -> None:
        """在活动交接到模型轮次前确保等待动画已经接管。"""
        if not self.task_state.turn_wait_active:
            return None
        await self.activity.ensure_wait()

    async def begin_upload_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动附件上传动画。"""
        await self.activity.begin_upload(snapshot)

    async def begin_download_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动运行时下载动画。"""
        await self.activity.begin_download(snapshot)

    async def begin_inbuild_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动内置运行时状态动画。"""
        await self.activity.begin_inbuild(snapshot)

    async def begin_external_mcp_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动外部 MCP 状态动画。"""
        await self.activity.begin_external_mcp(snapshot)

    async def begin_compact_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动对话压缩状态动画。"""
        await self.activity.begin_compact(snapshot)

    async def begin_operation_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动通用前台操作动画。"""
        await self.activity.begin_operation(snapshot)

    async def hold_activity_status(
        self,
        kind: ActivityStatusKind
    ) -> None:
        """保持指定活动的最终状态直至后续替换或清除。"""
        await self.activity.hold(kind)

    async def end_activity_status(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True
    ) -> None:
        """结束运行期活动动画。"""
        await self.activity.stop(kind, settle=settle)

    async def freeze_activity_status(
        self,
        kind: ActivityStatusKind,
    ) -> None:
        """冻结活动状态并等待后续可见结果接管。"""
        lease = self.activity.lease(kind)
        if lease is not None:
            self.activity.freeze(lease)

    async def run_modal(
        self,
        operation: typing.Callable[[], typing.Awaitable[ModalResult]]
    ) -> ModalResult:
        """暂时让出真实终端，并在外部交互结束后刷新几何状态。"""
        self._modal_depth += 1
        self.viewport.pause_scrollback()
        try:
            async with in_terminal(render_cli_done=False):
                return await operation()
        finally:
            self._modal_depth = max(0, self._modal_depth - 1)
            self.viewport.refresh_geometry()


def require_tui_runtime(runtime: FrontendRuntime) -> TuiRuntime:
    """验证前端运行期为 TUI 具体实现。"""
    if not isinstance(runtime, TuiRuntime):
        raise TypeError("TUI frontend requires TuiRuntime")
    return runtime


if __name__ == '__main__':
    pass
