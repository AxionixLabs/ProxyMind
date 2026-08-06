# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
import contextvars
from dataclasses import dataclass
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.application import in_terminal
from prompt_toolkit.input.base import Input
from prompt_toolkit.output.base import Output
from prompt_toolkit.patch_stdout import patch_stdout
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
    FrontendRuntime
)
from mind_app.interaction.contracts import PromptContext
from .models import (
    FragmentBlock,
    MenuRequest,
    TranscriptBacktrackRequest,
    TranscriptExportFormat,
    TranscriptExportResult
)
from .terminal_input import clear_pending_input
from .activity import (
    ActivityLease,
    TuiActivity,
)
from .document import (
    SourceBlockRenderer,
    TranscriptCellSource,
    TranscriptBlock,
    TuiBlockKind,
    TuiDocument,
    TuiDocumentState
)
from .input import TuiInputModel
from .interrupt import TuiExitReason
from .keymap import TuiRuntimeKeymap
from .process_viewer import ProcessViewerRequest
from .render import sanitize_fragment_block
from .queued import TuiSubmission
from .screen import TuiScreen
from .styles import (
    query_block,
    text_block
)
from ..prompting.commands import resolve_tui_command
from .submission import (
    TuiInputClosed,
    TuiInterruptRequested,
    TuiTranscriptBacktrackRequested,
    TuiSubmissionFlow
)
from .task_state import TuiTaskState
from .viewport import TuiTranscriptViewport

StartupAnimation: typing.TypeAlias = typing.Callable[
    [], typing.Awaitable[None]
]

ModalResult = typing.TypeVar("ModalResult")


@dataclass(frozen=True, slots=True)
class _DeferredBlock(object):
    """保存等待安全边界提交的正文块和活动租约。"""
    block: FragmentBlock
    transcript_block: FragmentBlock
    activity_lease: ActivityLease | None = None


class _ActivityHandoff(object):
    """保存当前任务结果接管活动区域的状态。"""

    __slots__ = ("lease", "deferred", "consumed")

    lease: ActivityLease | None
    deferred: bool
    consumed: bool

    def __init__(
        self,
        lease: ActivityLease | None,
        deferred: bool,
    ) -> None:
        self.lease    = lease
        self.deferred = deferred
        self.consumed = False


class _CommandLayoutHandoff(object):
    """保存当前命令结果对补全锚点的接管状态。"""

    __slots__ = ("consumed",)

    consumed: bool

    def __init__(self) -> None:
        self.consumed = False


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
            [tuple[TranscriptBlock, ...], TranscriptExportFormat],
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

        self._application_task: asyncio.Task[None] | None = None
        self._application_error: BaseException | None     = None
        self._consumed_submission: TuiSubmission | None   = None

        self._background_tasks: set[asyncio.Task[None]]               = set()
        self._background_session_tasks: dict[str, asyncio.Task[None]] = {}

        self._background_blocks: list[_DeferredBlock] = []

        self._activity_handoff = contextvars.ContextVar[
            _ActivityHandoff | None
        ]("tui_activity_handoff", default=None)

        self._command_layout = contextvars.ContextVar[
            _CommandLayoutHandoff | None
        ]("tui_command_layout", default=None)
        self._command_layout_token: contextvars.Token | None = None

        self._open_callbacks: list[typing.Callable[[], None]] = []
        self._startup_animations: list[StartupAnimation]      = []

        self._closing: bool    = False
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
            is_transcript_overlay_active=(
                lambda: self.screen.transcript_overlay.active
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
            settle_canvas_height=self._settle_stream_scrollback_layout,
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

        self.input_model.bind_history_backtrack(
            self._can_backtrack_history,
            self.open_transcript_backtrack,
            self._can_report_missing_backtrack,
            self._report_missing_backtrack,
        )
        self.input_model.bind_input_resize(self.screen.settle_input_layout)

        self.activity = TuiActivity(
            set_renderable=lambda block: self.screen.set_activity_renderable(
                sanitize_fragment_block(block)
            ),
            clear_renderable=self.screen.clear_activity_renderable,
            get_width=lambda: self.terminal_width,
            color_level=terminal_capabilities.color_level,
        )

    @property
    def active(self) -> bool:
        """返回 TUI 应用是否正在运行。"""
        task = self._application_task
        return task is not None and not task.done()

    @property
    def execution_active(self) -> bool:
        """返回模型轮次是否正在运行。"""
        return self.task_state.turn_running

    @property
    def foreground_active(self) -> bool:
        """返回是否正在等待下一轮开始前的前台屏障。"""
        return self.task_state.foreground_running

    @property
    def submission_deferred(self) -> bool:
        """返回新输入是否需要延迟到下一模型轮次。"""
        return self.execution_active or self.foreground_active

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
        """返回当前终端是否启用可点击文本链接。"""
        return self.terminal_capabilities.hyperlinks

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

    @execution_active.setter
    def execution_active(self, active: bool) -> None:
        """更新模型轮次运行状态。"""
        self.task_state.set_turn_running(active)

    def _terminal_geometry(self) -> tuple[int, int]:
        """通过单次尺寸快照返回当前终端宽高。"""
        geometry = self.screen.frame_geometry
        return geometry.width, geometry.height

    def _native_scrollback_deferred(self) -> bool:
        """判断当前运行状态是否禁止提交原生终端滚屏。"""
        if self._modal_depth > 0 or self.foreground_active:
            return True
        if not self.execution_active:
            return False
        return not (
            self.document.active_kind == "assistant"
            and self.document.active_stream_continuation
            and not self.screen.scrollback_interaction_active()
        )

    def _settle_stream_scrollback_layout(self) -> None:
        """在流式稳定前缀退休后收束其占用的实时画布。"""
        if (
            self.execution_active
            and self.document.active_kind == "assistant"
            and self.document.active_stream_continuation
        ):
            self.screen.settle_scrollback_layout()

    def _discard_submitted_query(self) -> None:
        """在二级菜单接管交互时撤下刚提交的输入块。"""
        if self.document.discard_submission():
            self.invalidate()
            return None
        if self.viewport.discard_submitted_query():
            self.screen.transcript_overlay.content_changed()

    def _application_task_exception(self) -> BaseException | None:
        """返回输入应用已经产生的终止异常。"""
        if self._application_error is not None:
            return self._application_error
        task = self._application_task
        if task is None or not task.done() or task.cancelled():
            return None
        return task.exception()

    def _flush_background_blocks(self) -> None:
        """在流式正文结束后提交已完成的后台摘要。"""
        if self.submission_deferred or self.document.active_block is not None:
            return None

        blocks = tuple(self._background_blocks)
        self._background_blocks.clear()

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
        blocks = tuple(self._background_blocks)
        self._background_blocks.clear()

        with self.screen.visual_update():
            for deferred in blocks:
                if deferred.activity_lease is not None:
                    self.activity.release(deferred.activity_lease)

    def _background_task_done(self, task: asyncio.Task[None]) -> None:
        """回收已完成的 TUI 后台任务。"""
        self._background_tasks.discard(task)
        for session_id, session_task in tuple(
            self._background_session_tasks.items()
        ):
            if session_task is task:
                self._background_session_tasks.pop(session_id, None)
        if not task.cancelled():
            task.exception()

    def _can_backtrack_history(self) -> bool:
        """返回主输入区是否可以开始历史编辑选择。"""
        return bool(
            self.active
            and not self.submission_deferred
            and not self.screen.transcript_overlay.active
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

    def configure_keymap(self, keymap: TuiRuntimeKeymap) -> None:
        """在 Application 启动前替换运行时按键映射。"""
        if self.active:
            raise RuntimeError("cannot configure TUI keymap while running")
        self.screen.set_keymap(keymap)
        self.keymap = keymap

    def configure_scrollback_reflow_line_limit(self, value: int) -> None:
        """配置终端尺寸变化时允许回放的最大逻辑行数。"""
        if self.active:
            raise RuntimeError(
                "cannot configure scrollback reflow while TUI is running"
            )
        self.viewport.configure_scrollback_reflow_line_limit(value)

    def set_startup_animation(
        self,
        animation: StartupAnimation
    ) -> None:
        """注册在 Application 首帧后播放的一次性启动动画。"""
        if self.active:
            raise RuntimeError("TUI startup animation requires an inactive runtime")
        if not self._startup_animations:
            self._startup_animations.append(animation)

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

    def set_prompt_context(self, context: PromptContext) -> None:
        """在首帧或输入轮次前更新输入区展示上下文。"""
        self.context = context

    def set_process_status_label(self, label: str) -> None:
        """更新动画区域下方的后台进程摘要。"""
        self.screen.process_status.set_label(label)

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

    def begin_process_viewer(
        self,
        request: ProcessViewerRequest,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> asyncio.Future[typing.Any]:
        """同步激活进程查看器并返回等待结果。"""
        self._discard_submitted_query()

        self.set_active_renderable(
            block,
            kind="operation",
            transcript_block=transcript_block,
        )

        return self.screen.process_viewer.begin(request)

    def update_process_viewer(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """替换当前动态进程正文。"""
        self.set_active_renderable(
            block,
            kind="operation",
            transcript_block=transcript_block,
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
        self.document.commit_active(block, transcript_block=transcript_block)
        self.screen.process_viewer.settle()
        self.screen.transcript_overlay.content_changed()
        self.viewport.stable_content_changed()
        self._flush_background_blocks()

    def dismiss_process_viewer(self) -> None:
        """撤下动态进程正文并恢复主输入区域。"""
        changed = self.document.active_kind == "operation"
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
        self._discard_submitted_query()

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
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)
        return task

    def start_background_session_task(
        self,
        session_id: str,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
    ) -> None:
        """为指定进程会话启动唯一的后台监视任务。"""
        sid = str(session_id or "").strip()
        self.cancel_background_session_task(sid)
        task = self.start_background_task(
            coroutine,
            name=f"process background {sid}",
        )
        self._background_session_tasks[sid] = task

    def cancel_background_session_task(self, session_id: str) -> None:
        """取消指定进程会话的后台监视任务。"""
        task = self._background_session_tasks.pop(
            str(session_id or "").strip(),
            None,
        )
        if task is not None and not task.done():
            task.cancel()

    def queue_background_block(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """在不打断流式正文的边界提交后台摘要。"""
        transcript_block = transcript_block or block
        if self.execution_active or self.document.active_block is not None:
            lease = self._consume_activity_handoff(deferred=True)
            self._background_blocks.append(_DeferredBlock(
                block,
                transcript_block,
                lease,
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
                activity_lease=self._consume_activity_handoff(
                    deferred=False,
                ),
            )

    def _append_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind,
        transcript_block: FragmentBlock | None = None,
        source: TranscriptCellSource | None = None,
        raw_text: str | None = None,
        stream_continuation: bool = False,
        activity_lease: ActivityLease | None = None,
    ) -> None:
        """在当前视觉事务中追加正文并完成相关状态交接。"""
        appended = self.document.append_block(
            block,
            kind=kind,
            transcript_block=transcript_block,
            source=source,
            raw_text=raw_text,
            stream_continuation=stream_continuation,
        )

        if activity_lease is not None:
            self.activity.release(activity_lease)

        if appended:
            self._complete_command_layout()
            self.screen.transcript_overlay.content_changed()
            self.viewport.content_appended()

    def replace_transcript(
        self,
        blocks: typing.Iterable[TranscriptBlock]
    ) -> None:
        """用恢复内容替换当前记录并重置终端视口。"""
        self.viewport.pause_scrollback()
        self.document.replace_blocks(blocks)

        self._discard_background_blocks()

        self.viewport.clear_submitted_query()
        self.viewport.reset_view()
        self.screen.transcript_overlay.content_replaced()
        self.screen.clear_terminal_scrollback()
        self.viewport.stable_content_changed()

    def discard_pending_submission(self) -> None:
        """清理由命令分派结束后仍未接管的暂存输入。"""
        if self.document.discard_submission():
            self.invalidate()

    def begin_command_layout(self) -> None:
        """标记当前输入可能通过命令结果收束补全占位。"""
        self.cancel_command_layout()
        self._command_layout_token = self._command_layout.set(
            _CommandLayoutHandoff()
        )

    def cancel_command_layout(self) -> None:
        """取消当前输入的命令收束标记并保留正文增长锚点。"""
        token = self._command_layout_token
        self._command_layout_token = None
        if token is not None:
            self._command_layout.reset(token)

    def finish_command_layout(self, *, force: bool = False) -> None:
        """结束命令布局并按需强制收束输入补全留下的临时空间。"""
        with self.screen.visual_update():
            completed = self._complete_command_layout()
            if force and not completed:
                self.screen.settle_completion_layout(invalidate=False)
                completed = True
            if completed:
                self.invalidate()
        self.cancel_command_layout()

    def _complete_command_layout(self) -> bool:
        """同步消费命令收束标记并更新最终画布高度。"""
        handoff = self._command_layout.get()
        if handoff is None or handoff.consumed:
            return False
        handoff.consumed = True
        self.screen.settle_completion_layout(invalidate=False)
        return True

    @property
    def command_layout_pending(self) -> bool:
        """返回当前任务是否仍等待命令结果收束补全锚点。"""
        handoff = self._command_layout.get()
        return handoff is not None and not handoff.consumed

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
            handoff = _ActivityHandoff(lease, deferred)

            if lease is not None and deferred:
                with self.screen.visual_update():
                    self.activity.freeze(lease)

            token = self._activity_handoff.set(handoff)
            try:
                yield
            finally:
                self._activity_handoff.reset(token)
                if lease is not None and not handoff.consumed:
                    with self.screen.visual_update():
                        self.activity.release(lease)

        return transaction()

    def _consume_activity_handoff(
        self,
        *,
        deferred: bool,
    ) -> ActivityLease | None:
        """消费当前任务等待交接的活动租约。"""
        handoff = self._activity_handoff.get()
        if handoff is None or handoff.consumed:
            return None

        lease = handoff.lease
        if lease is None:
            handoff.consumed = True
            return None

        if deferred and not handoff.deferred:
            self.activity.freeze(lease)
            handoff.deferred = True

        handoff.consumed = True
        return lease

    def replace_input_text(self, text: str) -> None:
        """替换主输入内容并把光标移动到末尾。"""
        value = str(text)

        buffer = self.screen.input.buffer
        buffer.text = value
        buffer.cursor_position = len(value)

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
        stream_continuation: bool = False
    ) -> None:
        """替换当前流式展示块。"""
        with self.screen.visual_update():
            self.document.set_active(
                block,
                kind=kind,
                transcript_block=transcript_block,
                raw_text=raw_text,
                stream_continuation=stream_continuation,
            )

            self.screen.transcript_overlay.content_changed()
            self.invalidate()

    def invalidate(self) -> None:
        """请求重新绘制当前稳定画布。"""
        self.screen.invalidate()

    def toggle_transcript_overlay(self) -> None:
        """切换完整会话记录并协调原生滚屏任务。"""
        self.input_model.cancel_history_backtrack()
        active = not self.screen.transcript_overlay.active

        if active:
            self._open_transcript_overlay()
            return None

        if self.screen.set_transcript_overlay(False):
            self.viewport.schedule_scrollback_flush()

    def open_transcript_backtrack(self) -> None:
        """从主输入区打开完整记录并选择最近用户轮次。"""
        if not self._open_transcript_overlay():
            return None

        if self.screen.transcript_overlay.begin_or_step_backtrack():
            return None

        self.screen.set_transcript_overlay(False)
        self.viewport.schedule_scrollback_flush()

    def _open_transcript_overlay(self) -> bool:
        """冻结原生滚屏后切换到完整记录画面。"""
        self.viewport.pause_scrollback()
        try:
            opened = self.screen.set_transcript_overlay(True)
        except BaseException:
            self.viewport.schedule_scrollback_flush()
            raise

        if not opened:
            self.viewport.schedule_scrollback_flush()
        return opened

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
        with self.screen.visual_update():
            assistant_stream = self.document.active_kind == "assistant"
            self.document.commit_active(
                block,
                transcript_block=transcript_block,
                raw_text=raw_text,
                source_renderer=source_renderer,
                source_render_width=source_render_width,
            )
            self.screen.transcript_overlay.content_changed()
            if assistant_stream:
                self.viewport.stream_finalized()
            self.viewport.stable_content_changed()
            self._flush_background_blocks()

    def commit_active_stream_prefix(
        self,
        block: FragmentBlock,
        *,
        raw_text: str
    ) -> None:
        """提交流式正文的稳定前缀并继续保留当前执行周期。"""
        self.document.commit_active(block, raw_text=raw_text)
        self.screen.transcript_overlay.content_changed()
        self.viewport.stream_content_changed()

    def clear_active_renderable(self) -> None:
        """清空当前流式展示块。"""
        assistant_stream = self.document.active_kind == "assistant"
        self.document.clear_active()
        self.screen.transcript_overlay.content_changed()
        if assistant_stream:
            self.viewport.stream_finalized()
        self.viewport.stable_content_changed()
        self._flush_background_blocks()

    def set_execution_active(self, active: bool) -> None:
        """更新模型轮次执行状态并切换输入区布局。"""
        was_active = self.execution_active
        self.execution_active = bool(active)
        if self.execution_active:
            self.viewport.clear_submitted_query()
        else:
            if was_active:
                self.screen.settle_completion_layout()
            self.submissions.clear_queued_submission_marker()
            self._flush_background_blocks()

        self.invalidate()

        if not self.execution_active:
            self.viewport.schedule_scrollback_flush()

    def set_foreground_active(self, active: bool) -> None:
        """更新下一轮开始前的前台屏障状态。"""
        self.task_state.set_foreground_running(active)

        if not self.submission_deferred:
            self.submissions.clear_queued_submission_marker()
            self._flush_background_blocks()
            self.viewport.schedule_scrollback_flush()

        self.invalidate()

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
        handler: typing.Callable[[TuiSubmission, bool], bool] | None,
    ) -> None:
        """绑定或清除活动模型轮次的输入接管函数。"""
        self.submissions.bind_turn_input_handler(handler)

    def bind_queued_restore_handler(
        self,
        handler: typing.Callable[[TuiSubmission], None] | None,
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

    async def _run_application(self) -> None:
        """运行输入应用并传播终端结束状态。"""
        application = self.screen.application
        try:
            with create_app_session(
                input=application.input,
                output=application.output,
            ):
                with patch_stdout(raw=True):
                    await application.run_async(
                        pre_run=lambda: clear_pending_input(application.input)
                    )
        except (EOFError, KeyboardInterrupt) as exc:
            self._application_error = exc
        finally:
            if not self._closing:
                self.submissions.finish_input()

    async def _exit_application(self, *, erase: bool) -> None:
        """结束当前应用任务并按需清除画布。"""
        task = self._application_task
        if task is None:
            return None

        application = self.screen.application
        application.erase_when_done = erase

        if not application.is_done:
            with contextlib.suppress(Exception):
                application.exit(result=None)

        await asyncio.gather(task, return_exceptions=True)
        self.screen.reset_synchronized_output()

        self._application_task      = None
        application.erase_when_done = False

    async def _play_startup_animation(self) -> None:
        """播放并清除当前注册的启动动画。"""
        animations = tuple(self._startup_animations)
        self._startup_animations.clear()
        for animation in animations:
            await animation()

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

        self._application_error = None

        self.screen.set_transcript_only(False)

        application = self.screen.application

        previous_render_count = application.render_counter

        self._application_task = asyncio.create_task(
            self._run_application(),
            name="tui application",
        )

        while (
            (
                not application.is_running
                or application.render_counter == previous_render_count
            )
            and not self._application_task.done()
        ):
            await asyncio.sleep(0)

        application_error = self._application_task_exception()
        if application_error is not None:
            raise application_error

        await self._play_startup_animation()

        self.viewport.refresh_geometry()

        for callback in tuple(self._open_callbacks):
            callback()

    async def close(self) -> None:
        """停止输入应用和全部动态任务。"""
        self._closing = True

        self._turn_progress_active = False

        preserve_transcript = self.document.has_conversation

        self._startup_animations.clear()
        self.terminal_progress.clear()

        await self.submissions.close()
        await self.activity.clear()

        self.task_state.clear()
        self.screen.process_status.clear()

        await self.screen.approval.close()
        await self.screen.menu.close()
        await self.screen.process_viewer.close()

        if self.document.active_kind == "operation":
            self.document.clear_active()

        self.document.discard_submission()
        self.screen.bottom_pane.clear()

        if self.screen.transcript_overlay.active:
            self.screen.set_transcript_overlay(False)

        self.screen.set_transcript_only(preserve_transcript)

        background_tasks = tuple(self._background_tasks)

        self._background_tasks.clear()
        self._background_session_tasks.clear()

        for task in background_tasks:
            task.cancel()

        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

        await self.viewport.close()
        await self._exit_application(erase=not preserve_transcript)

    async def read_message(self, context: PromptContext) -> str:
        """更新输入上下文并按提交顺序读取下一条消息。"""
        self.set_prompt_context(context)

        try:
            submission = await self.submissions.read_submission()
        except TuiInputClosed:
            application_error = self._application_task_exception()
            if application_error is not None:
                if isinstance(application_error, KeyboardInterrupt):
                    raise TuiInterruptRequested from application_error
                raise application_error
            raise EOFError

        if isinstance(submission, TranscriptBacktrackRequest):
            raise TuiTranscriptBacktrackRequested(submission)

        if isinstance(submission, TuiSubmission):
            self._consumed_submission = submission
            value = submission.value
            visible = submission.visible_text.strip() or value
        else:
            self._consumed_submission = None
            value = str(submission)
            visible = value

        self.viewport.reset_view()

        if visible:
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
                    self.viewport.mark_submitted_query(committed)

        self.submissions.clear_surface_submission_pending()

        return value

    async def select_menu(self, request: MenuRequest) -> typing.Any:
        """在主 Application 画布内读取菜单选择。"""
        self._discard_submitted_query()
        return await self.screen.menu.request(request)

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
        transcript_block: FragmentBlock | None = None
    ) -> typing.Any:
        """显示动态进程正文并等待查看器动作。"""
        future = self.begin_process_viewer(
            request,
            block,
            transcript_block=transcript_block,
        )

        try:
            return await future
        except BaseException:
            self.dismiss_process_viewer()
            raise

    async def begin_wait_status(self) -> None:
        """启动覆盖当前交互周期的等待动画。"""
        await self.activity.begin_wait()

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
