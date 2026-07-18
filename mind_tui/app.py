# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from pathlib import Path
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import BufferControl
from mind_app.runtime.support.clipboard import (
    ClipboardError, copy_text_to_clipboard
)
from mind_nova import const
from .approval import (
    ApprovalDecision, ApprovalOverlay
)
from .bottom_pane import BottomPaneRenderer
from .commands import CommandCompleter
from .dispatch import AppEventDispatcher
from .input_events import InputEvent
from .key_bindings import (
    InputBindingActions, create_key_bindings
)
from .layout import create_tui_layout
from .projector import (
    AppEventProjector, ProjectionActions
)
from .runtime import (
    StreamProvider, TurnRequest
)
from .scrollbar import (
    ScrollbarControl, StreamBufferControl
)
from .state import (
    QueuedMessage, TuiState, consume_shell_prefix
)
from .transcript import (
    RenderedTranscript,
    TranscriptCellKind,
    TranscriptLexer,
    TranscriptRenderer
)

_STATUS_FRAME_INTERVAL = 0.6


class TuiApp:
    """运行通过可替换运行时驱动的独立终端应用。"""

    def __init__(
        self,
        *,
        model_label: str = "gpt-5.6-sol high",
        workspace_label: str | None = None,
        stream_provider: StreamProvider
    ) -> None:
        """初始化应用状态、终端组件和事件调度器。"""
        self.model_label     = model_label
        self.workspace_label = workspace_label or str(Path.cwd())
        self.stream_provider = stream_provider

        self.state = TuiState(self._initial_transcript())

        self._changing_input: bool = False

        self._tasks: set[asyncio.Task[typing.Any]] = set()

        self._turn_lock = asyncio.Lock()

        self._status_task: asyncio.Task[typing.Any] | None      = None
        self._active_turn_task: asyncio.Task[typing.Any] | None = None

        self._closing: bool = False

        self.command_completer = CommandCompleter()

        self.transcript_renderer = TranscriptRenderer()
        self.rendered_transcript: RenderedTranscript = (
            self.transcript_renderer.render(self.state.transcript.cells)
        )
        transcript = self.rendered_transcript.text
        self.output_buffer = Buffer(
            document=Document(transcript, cursor_position=len(transcript)),
            read_only=True,
            multiline=True
        )
        self.input_buffer = Buffer(
            completer=self.command_completer,
            complete_while_typing=True,
            multiline=True,
            accept_handler=self._accept_input,
            on_text_changed=self._on_input_changed
        )
        self.output_control = StreamBufferControl(
            buffer=self.output_buffer,
            lexer=TranscriptLexer(lambda: self.rendered_transcript),
            focusable=True,
            focus_on_click=True,
            on_scroll=self._scroll_output_by
        )
        self.input_control = BufferControl(
            buffer=self.input_buffer,
            focusable=True,
            focus_on_click=True
        )
        self.output_window = Window(
            self.output_control,
            wrap_lines=True,
            dont_extend_height=True,
            always_hide_cursor=True
        )
        self.scrollbar_control = ScrollbarControl(
            get_render_info=lambda: self.output_window.render_info,
            on_scroll_by=self._scroll_output_by,
            on_scroll_to=self._scroll_output_to
        )

        self.approval = ApprovalOverlay()
        self.bottom_renderer = BottomPaneRenderer(
            state=self.state,
            input_buffer=self.input_buffer,
            model_label=self.model_label,
            workspace_label=self.workspace_label
        )
        self.key_bindings = create_key_bindings(
            state=self.state,
            input_buffer=self.input_buffer,
            output_buffer=self.output_buffer,
            input_control=self.input_control,
            output_window=self.output_window,
            actions=InputBindingActions(
                dispatch=self._handle_input_event,
                scroll_output=self._scroll_output_by,
                copy_selection=self._schedule_copy_selection
            )
        )
        layout = create_tui_layout(
            state=self.state,
            input_control=self.input_control,
            output_window=self.output_window,
            scrollbar_control=self.scrollbar_control,
            approval=self.approval,
            renderer=self.bottom_renderer,
            key_bindings=self.key_bindings
        )
        self.application: Application[None] = layout.application

        self.input_window            = layout.input_window
        self.status_top_spacer       = layout.status_top_spacer
        self.status_container        = layout.status_container
        self.queue_panel             = layout.queue_panel
        self.composer_row            = layout.composer_row
        self.composer_hint_container = layout.composer_hint_container
        self.footer_window           = layout.footer_window
        self.application.ttimeoutlen = 0.05

        self.approval.bind(self.application)

        self.event_projector = AppEventProjector(
            ProjectionActions(
                set_status=self._set_status,
                write_stream=self._write_stream,
                finish_stream=self._finish_stream,
                write_block=lambda text, kind: self._write_block(text, kind=kind),
                request_approval=self._request_approval
            )
        )
        self.event_dispatcher = AppEventDispatcher(self.event_projector.project)

    async def run(self) -> None:
        """启动终端界面并在退出时回收后台任务。"""
        self.event_dispatcher.start()
        try:
            await self.application.run_async()
        finally:
            self._closing = True
            for task in tuple(self._tasks):
                task.cancel()
            if self._status_task is not None:
                self._status_task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self.event_dispatcher.stop()

    def prompt_symbol(self) -> str:
        """返回当前输入模式的提示符。"""
        return self.bottom_renderer.prompt_symbol()

    def _initial_transcript(self) -> str:
        """生成主视口初始内容。"""
        return f">_ {const.APP_DESC} (v{const.APP_VERSION})"

    def _on_input_changed(self, buffer: Buffer) -> None:
        """消费 shell 模式前缀并同步提示符。"""
        if self._changing_input:
            return

        text, cursor, shell_mode = consume_shell_prefix(
            buffer.text,
            buffer.cursor_position,
            shell_mode=self.state.composer.shell_mode
        )
        if text == buffer.text and shell_mode == self.state.composer.shell_mode:
            return

        self.state.composer.shell_mode = shell_mode

        self._changing_input = True

        try:
            buffer.set_document(
                Document(text, cursor_position=cursor),
                bypass_readonly=True
            )
        finally:
            self._changing_input = False
        self.application.invalidate()

    def _accept_input(self, buffer: Buffer) -> bool:
        """把当前编辑内容转换为提交事件。"""
        message = buffer.text.strip()
        if not message:
            return False

        shell_mode = self.state.composer.shell_mode

        self.state.composer.shell_mode = False

        self._handle_input_event(
            InputEvent("submit", text=message, shell_mode=shell_mode)
        )
        return False

    def _handle_input_event(self, event: InputEvent) -> None:
        """把输入语义事件路由到当前应用状态。"""
        if event.kind == "submit":
            if self.state.turn.busy:
                self.state.turn.enqueue(event.text, shell_mode=event.shell_mode)
                self._interrupt_active_turn()
                return
            self._start_message(
                QueuedMessage(event.text, shell_mode=event.shell_mode)
            )
            return
        if event.kind == "queue":
            self._queue_current_input()
            return
        if event.kind == "rollback":
            self._rollback_queued_message()
            return
        if event.kind == "interrupt":
            self._interrupt_active_turn()

    async def _run_submission(self, message: str, *, shell_mode: bool) -> None:
        """通过运行时提交输入并顺序消费事件。"""
        current_task = asyncio.current_task()
        try:
            async with self._turn_lock:
                request = TurnRequest(message, shell_mode=shell_mode)
                async for event in self.stream_provider.stream(request):
                    await self.event_dispatcher.dispatch(event)
                    if (
                        event.kind in {"tool.call", "tool.output"}
                        and self.state.turn.queued_messages
                    ):
                        break
        finally:
            self._set_status("")
            if self._active_turn_task is current_task:
                self._active_turn_task = None
                self.state.turn.busy = False
                if not self._closing:
                    self._drain_queue()

    def _start_message(self, item: QueuedMessage) -> None:
        """把消息写入会话并在需要时启动运行时。"""
        self.state.turn.submitted.append(item.text)
        prefix = "!" if item.shell_mode else "›"
        self._write_block(f"{prefix} {item.text}", kind="user")
        if item.text.startswith("/") and item.text != "/approval":
            return

        self.state.turn.busy = True
        self._set_status("Working")

        task = asyncio.create_task(
            self._run_submission(item.text, shell_mode=item.shell_mode)
        )
        self._active_turn_task = task
        self._track_task(task)
        self.application.invalidate()

    def _queue_current_input(self) -> None:
        """把当前输入加入队列并清空编辑区。"""
        message = self.input_buffer.text.strip()
        if not message:
            return

        self.state.turn.enqueue(
            message,
            shell_mode=self.state.composer.shell_mode
        )

        self.state.composer.shell_mode = False

        self.input_buffer.reset()
        self.application.invalidate()

    def _rollback_queued_message(self) -> None:
        """把最近排队的消息恢复到输入区。"""
        item = self.state.turn.rollback()
        if item is None:
            return

        self.state.composer.shell_mode = item.shell_mode

        self.input_buffer.set_document(
            Document(item.text, cursor_position=len(item.text)),
            bypass_readonly=True
        )
        self.application.invalidate()

    def _interrupt_active_turn(self) -> None:
        """中断当前运行时任务并立即调度队首消息。"""
        task = self._active_turn_task
        if task is not None and not task.done():
            task.cancel()
            return
        self.state.turn.busy = False
        self._drain_queue()

    def _drain_queue(self) -> None:
        """按顺序启动下一条等待消息。"""
        if self._closing or self.state.turn.busy:
            return
        while item := self.state.turn.dequeue():
            self._start_message(item)
            if self.state.turn.busy:
                break
        self.application.invalidate()

    async def _request_approval(
        self,
        payload: dict[str, typing.Any]
    ) -> ApprovalDecision:
        """将审批视图压入底部区域并等待用户决定。"""
        self.input_buffer.cancel_completion()
        self.state.bottom_pane.push("approval", payload)
        try:
            return await self.approval.show(payload)
        finally:
            self.state.bottom_pane.pop("approval")

    def _write_stream(self, text: str) -> None:
        """按全局规则写入流式正文。"""
        if self.state.transcript.write_stream(text):
            self._sync_transcript()

    def _finish_stream(self) -> None:
        """结束当前流式正文并同步段尾。"""
        if self.state.transcript.finish_stream():
            self._sync_transcript()

    def _write_block(
        self,
        text: str,
        *,
        kind: TranscriptCellKind = "trace"
    ) -> None:
        """按全局规则写入完整内容块。"""
        if self.state.transcript.write_block(text, kind=kind):
            self._sync_transcript()

    def _sync_transcript(self) -> None:
        """同步归一化正文并在跟随模式下保持视口末尾。"""
        self.rendered_transcript = self.transcript_renderer.render(
            self.state.transcript.cells
        )
        transcript = self.rendered_transcript.text
        selection  = self.output_buffer.selection_state
        cursor     = self.output_buffer.cursor_position

        if self.state.viewport.follow_tail and selection is None:
            cursor = len(transcript)
        else:
            cursor = min(cursor, len(transcript))
        self.output_buffer.set_document(
            Document(transcript, cursor_position=cursor),
            bypass_readonly=True
        )
        self.application.invalidate()

    def _scroll_output_by(self, amount: int) -> None:
        """按行数滚动正文并同步末尾跟随状态。"""
        info = self.output_window.render_info
        if info is None:
            return

        step = (
            self.output_window._scroll_down
            if amount > 0
            else self.output_window._scroll_up
        )

        for _ in range(abs(int(amount))):
            step()

        maximum = max(0, info.content_height - info.window_height)

        self.state.viewport.follow_tail = (
            self.output_window.vertical_scroll >= maximum
        )
        self.application.invalidate()

    def _scroll_output_to(self, offset: int) -> None:
        """把正文视口移动到指定滚动偏移。"""
        info = self.output_window.render_info
        if info is None:
            return

        maximum = max(0, info.content_height - info.window_height)
        target  = min(maximum, max(0, int(offset)))

        self.output_window.vertical_scroll = target
        self.state.viewport.follow_tail = target >= maximum

        document = self.output_buffer.document

        if document.line_count > 0:
            ratio = target / maximum if maximum > 0 else 0.0
            row   = round((document.line_count - 1) * ratio)

            self.output_buffer.exit_selection()
            self.output_buffer.cursor_position = (
                document.translate_row_col_to_index(row, 0)
            )

        self.application.invalidate()

    def _set_status(self, text: str) -> None:
        """更新固定状态区并管理动画任务。"""
        self.state.status.set(text)
        if self.state.status.text:
            if self._status_task is None or self._status_task.done():
                self._status_task = asyncio.create_task(self._animate_status())
        elif self._status_task is not None:
            self._status_task.cancel()
            self._status_task = None
        self.application.invalidate()

    async def _animate_status(self) -> None:
        """刷新固定状态区的轻量动画。"""
        while self.state.status.text:
            await asyncio.sleep(_STATUS_FRAME_INTERVAL)
            self.state.status.tick()
            self.application.invalidate()

    def _schedule_copy_selection(self, text: str) -> None:
        """调度选中文本的剪贴板写入任务。"""
        self._track_task(asyncio.create_task(self._copy_selection(text)))

    async def _copy_selection(self, text: str) -> None:
        """把鼠标选中的正文复制到系统剪贴板。"""
        if not text:
            return
        with contextlib.suppress(ClipboardError):
            await copy_text_to_clipboard(text)

    def _track_task(self, task: asyncio.Task[typing.Any]) -> None:
        """保存后台任务并在完成后移除引用。"""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


if __name__ == '__main__':
    pass
