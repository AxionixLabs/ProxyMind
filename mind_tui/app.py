# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from pathlib import Path
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.lexers import Lexer
from mind_app.runtime.support.clipboard import ClipboardError, copy_text_to_clipboard
from mind_nova import const
from .approval import ApprovalOverlay
from .commands import CommandCompleter, create_command_menu
from .events import TuiEvent, simulate_stream
from .scrollbar import ScrollbarControl, StreamBufferControl
from .state import QueuedMessage, TuiState, consume_shell_prefix
from .style import TUI_STYLE

_STATUS_FRAMES = ("·", "•", "●", "•")


class TranscriptLexer(Lexer):
    """按会话行类型为只读正文提供轻量样式。"""

    def lex_document(self, document: Document):
        """返回逐行样式解析函数。"""
        lines = document.lines

        def get_line(lineno: int) -> StyleAndTextTuples:
            if lineno >= len(lines):
                return []
            line = lines[lineno]
            return [(self._line_style(line), line)]

        return get_line

    @staticmethod
    def _line_style(line: str) -> str:
        """返回单行对应的界面样式。"""
        stripped = line.lstrip()
        if line.startswith(">_ Mind"):
            return "class:header"
        if stripped.startswith("Tip:"):
            return "class:tip.text"
        if line.startswith(("› ", "! ")):
            return "class:user"
        if stripped.startswith(("└", "├", "│")):
            return "class:trace"
        if stripped.startswith("• "):
            return "class:trace.title"
        return "class:assistant"


class TuiApp:
    """运行不连接现有业务链路的完整终端界面原型。"""

    def __init__(
        self,
        *,
        model_label: str = "gpt-5.6-sol high",
        workspace_label: str | None = None,
        stream_delay: float = 0.025
    ) -> None:
        self.model_label = model_label
        self.workspace_label = workspace_label or str(Path.cwd())
        self.stream_delay = max(0.0, float(stream_delay))
        self.state = TuiState(self._initial_transcript())
        self._changing_input = False
        self._tasks: set[asyncio.Task[typing.Any]] = set()
        self._turn_lock = asyncio.Lock()
        self._status_task: asyncio.Task[typing.Any] | None = None
        self._active_turn_task: asyncio.Task[typing.Any] | None = None
        self._closing = False
        self.command_completer = CommandCompleter()

        self.output_buffer = Buffer(
            document=Document(self.state.transcript, cursor_position=len(self.state.transcript)),
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
            lexer=TranscriptLexer(),
            focusable=True,
            focus_on_click=True,
            on_scroll=self._scroll_output_by
        )
        self.input_control = BufferControl(buffer=self.input_buffer, focusable=True)
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
        self.key_bindings = self._build_key_bindings()
        self.application: Application[None] = self._build_application()
        self.application.ttimeoutlen = 0.05
        self.approval.bind(self.application)

    async def run(self) -> None:
        """启动终端界面并在退出时回收模拟任务。"""
        try:
            await self.application.run_async()
        finally:
            self._closing = True
            for task in tuple(self._tasks):
                task.cancel()
            if self._status_task is not None:
                self._status_task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def prompt_symbol(self) -> str:
        """返回当前输入模式的提示符。"""
        return "!" if self.state.shell_mode else "›"

    def _initial_transcript(self) -> str:
        """生成主视口初始内容。"""
        return (
            f">_ {const.APP_DESC} (v{const.APP_VERSION})\n\n"
            f"  Tip: New Build faster with {const.APP_DESC}."
        )

    def _build_application(self) -> Application[None]:
        """创建应用布局和浮层。"""
        prompt = Window(
            FormattedTextControl(self._prompt_fragments),
            width=2,
            dont_extend_width=True,
            always_hide_cursor=True
        )
        self.input_window = Window(
            self.input_control,
            height=Dimension(min=1, max=6),
            wrap_lines=True,
            dont_extend_height=True,
            style="class:input"
        )
        status_window = Window(
            FormattedTextControl(self._status_fragments),
            height=1,
            always_hide_cursor=True
        )
        self.queue_panel = ConditionalContainer(
            Window(
                FormattedTextControl(self._queue_fragments),
                wrap_lines=True,
                dont_extend_height=True,
                always_hide_cursor=True
            ),
            filter=Condition(lambda: bool(self.state.queued_messages))
        )
        self.composer_hint_window = Window(
            FormattedTextControl(self._composer_hint_fragments),
            height=1,
            always_hide_cursor=True
        )
        self.footer_window = Window(
            FormattedTextControl(self._footer_fragments),
            height=1,
            align="CENTER",
            always_hide_cursor=True
        )

        body = HSplit([
            self.output_window,
            self.queue_panel,
            status_window,
            VSplit([prompt, self.input_window]),
            self.composer_hint_window,
            create_command_menu(),
            self.footer_window
        ])
        surface = VSplit([
            body,
            Window(
                self.scrollbar_control,
                width=1,
                dont_extend_width=True,
                always_hide_cursor=True
            )
        ])
        root = FloatContainer(
            content=surface,
            floats=[Float(content=self.approval.container, z_index=20)]
        )
        return Application(
            layout=Layout(root, focused_element=self.input_control),
            key_bindings=self.key_bindings,
            style=TUI_STYLE,
            full_screen=True,
            mouse_support=True,
            erase_when_done=False,
            refresh_interval=0.1
        )

    def _build_key_bindings(self) -> KeyBindings:
        """创建主界面的按键绑定。"""
        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event) -> None:
            if event.app.current_buffer is self.input_buffer:
                state = self.input_buffer.complete_state
                if state is not None and state.current_completion is not None:
                    self.input_buffer.apply_completion(state.current_completion)
                    return
                self.input_buffer.validate_and_handle()

        @bindings.add("tab")
        def _complete(event) -> None:
            if event.app.current_buffer is not self.input_buffer:
                return
            state = self.input_buffer.complete_state
            if state is not None and state.completions:
                completion = state.current_completion or state.completions[0]
                self.input_buffer.apply_completion(completion)
                return
            if self.state.busy and self.input_buffer.text.strip():
                self._queue_current_input()
                return
            self.input_buffer.start_completion(select_first=False)

        @bindings.add("c-o")
        def _newline(event) -> None:
            if event.app.current_buffer is self.input_buffer:
                self.input_buffer.insert_text("\n")

        @bindings.add("escape", eager=True)
        def _interrupt_and_send(event) -> None:
            if event.app.current_buffer is not self.input_buffer:
                return
            if self.input_buffer.complete_state is not None:
                self.input_buffer.cancel_completion()
                return
            if self.state.busy and self.state.queued_messages:
                self._interrupt_active_turn()

        queue_rollback = Condition(
            lambda: (
                self.application.layout.current_buffer is self.input_buffer
                and not self.input_buffer.text
                and bool(self.state.queued_messages)
            )
        )

        @bindings.add(Keys.ControlLeft, filter=queue_rollback, eager=True)
        def _rollback_queue(event) -> None:
            self._rollback_queued_message()

        @bindings.add("backspace", eager=True)
        def _backspace(event) -> None:
            if event.app.current_buffer is not self.input_buffer:
                return
            if not self.input_buffer.text and self.state.shell_mode:
                self.state.shell_mode = False
                event.app.invalidate()
                return
            self.input_buffer.delete_before_cursor(count=max(1, event.arg))

        @bindings.add("c-u", eager=True)
        def _clear_input(event) -> None:
            if event.app.current_buffer is self.input_buffer:
                self.input_buffer.reset()
                self.state.shell_mode = False
                event.app.invalidate()

        @bindings.add("c-c")
        def _copy_or_cancel(event) -> None:
            if event.app.current_buffer is self.output_buffer and self.output_buffer.selection_state:
                data = self.output_buffer.copy_selection()
                self._track_task(asyncio.create_task(self._copy_selection(data.text)))
                event.app.layout.focus(self.input_control)
                return
            if self.input_buffer.text:
                self.input_buffer.reset()
                self.state.shell_mode = False
                return
            event.app.exit(result=None)

        @bindings.add("pageup")
        def _page_up(event) -> None:
            info = self.output_window.render_info
            self._scroll_output_by(-max(1, (info.window_height - 1) if info else 1))

        @bindings.add("pagedown")
        def _page_down(event) -> None:
            info = self.output_window.render_info
            self._scroll_output_by(max(1, (info.window_height - 1) if info else 1))

        return bindings

    def _on_input_changed(self, buffer: Buffer) -> None:
        """消费 shell 模式前缀并同步提示符。"""
        if self._changing_input:
            return
        text, cursor, shell_mode = consume_shell_prefix(
            buffer.text,
            buffer.cursor_position,
            shell_mode=self.state.shell_mode
        )
        if text == buffer.text and shell_mode == self.state.shell_mode:
            return

        self.state.shell_mode = shell_mode
        self._changing_input = True
        try:
            buffer.set_document(Document(text, cursor_position=cursor), bypass_readonly=True)
        finally:
            self._changing_input = False
        self.application.invalidate()

    def _accept_input(self, buffer: Buffer) -> bool:
        """提交当前输入并启动一轮模拟流。"""
        message = buffer.text.strip()
        if not message:
            return False

        shell_mode = self.state.shell_mode
        self.state.shell_mode = False
        if self.state.busy:
            self.state.enqueue(message, shell_mode=shell_mode)
            self._interrupt_active_turn()
            return False

        self._start_message(QueuedMessage(message, shell_mode=shell_mode))
        return False

    async def _run_submission(self, message: str, *, shell_mode: bool) -> None:
        """顺序消费一轮模拟事件。"""
        current_task = asyncio.current_task()
        try:
            async with self._turn_lock:
                async for event in simulate_stream(
                    message,
                    shell_mode=shell_mode,
                    delay=self.stream_delay
                ):
                    await self._consume_event(event)
                    if event.kind == "text.block" and self.state.queued_messages:
                        break
        finally:
            self._set_status("")
            if self._active_turn_task is current_task:
                self._active_turn_task = None
                self.state.busy = False
                if not self._closing:
                    self._drain_queue()

    def _start_message(self, item: QueuedMessage) -> None:
        """把消息写入会话并在需要时启动模拟流。"""
        self.state.submitted.append(item.text)
        prefix = "!" if item.shell_mode else "›"
        self._append_transcript(f"\n{prefix} {item.text}\n\n")
        if item.text.startswith("/") and item.text != "/approval":
            return

        self.state.busy = True
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
        self.state.enqueue(message, shell_mode=self.state.shell_mode)
        self.state.shell_mode = False
        self.input_buffer.reset()
        self.application.invalidate()

    def _rollback_queued_message(self) -> None:
        """把最近排队的消息恢复到输入区。"""
        item = self.state.rollback_queue()
        if item is None:
            return
        self.state.shell_mode = item.shell_mode
        self.input_buffer.set_document(
            Document(item.text, cursor_position=len(item.text)),
            bypass_readonly=True
        )
        self.application.invalidate()

    def _interrupt_active_turn(self) -> None:
        """中断当前模拟流并立即调度队首消息。"""
        task = self._active_turn_task
        if task is not None and not task.done():
            task.cancel()
            return
        self.state.busy = False
        self._drain_queue()

    def _drain_queue(self) -> None:
        """按顺序启动下一条等待消息。"""
        if self._closing or self.state.busy:
            return
        while item := self.state.dequeue():
            self._start_message(item)
            if self.state.busy:
                break
        self.application.invalidate()

    async def _consume_event(self, event: TuiEvent) -> None:
        """把单条模拟事件投影到界面状态。"""
        if event.kind == "status":
            self._set_status(event.text)
            return
        if event.kind == "text.block":
            suffix = "" if event.text.endswith("\n") else "\n"
            self._append_transcript(event.text + suffix)
            return
        if event.kind == "text.delta":
            self._append_transcript(event.text)
            return
        if event.kind == "approval.required":
            self.state.approval_visible = True
            try:
                decision = await self.approval.show(event.payload or {})
            finally:
                self.state.approval_visible = False
            label = {
                "accept": "approved",
                "acceptForSession": "approved for this session",
                "decline": "declined"
            }[decision]
            self._append_transcript(f"• Approval {label}\n\n")
            return
        if event.kind == "done":
            self._append_transcript("\n")

    def _append_transcript(self, text: str) -> None:
        """追加正文并在跟随模式下保持视口位于末尾。"""
        selection = self.output_buffer.selection_state
        cursor = self.output_buffer.cursor_position
        self.state.append(text)
        if self.state.follow_tail and selection is None:
            cursor = len(self.state.transcript)
        else:
            cursor = min(cursor, len(self.state.transcript))
        self.output_buffer.set_document(
            Document(self.state.transcript, cursor_position=cursor),
            bypass_readonly=True
        )
        self.application.invalidate()

    def _scroll_output_by(self, amount: int) -> None:
        """按行数滚动正文并同步末尾跟随状态。"""
        info = self.output_window.render_info
        if info is None:
            return
        step = self.output_window._scroll_down if amount > 0 else self.output_window._scroll_up
        for _ in range(abs(int(amount))):
            step()
        maximum = max(0, info.content_height - info.window_height)
        self.state.follow_tail = self.output_window.vertical_scroll >= maximum
        self.application.invalidate()

    def _scroll_output_to(self, offset: int) -> None:
        """把正文视口移动到指定滚动偏移。"""
        info = self.output_window.render_info
        if info is None:
            return
        maximum = max(0, info.content_height - info.window_height)
        target = min(maximum, max(0, int(offset)))
        self.output_window.vertical_scroll = target
        self.state.follow_tail = target >= maximum

        document = self.output_buffer.document
        if document.line_count > 0:
            ratio = target / maximum if maximum > 0 else 0.0
            row = round((document.line_count - 1) * ratio)
            self.output_buffer.exit_selection()
            self.output_buffer.cursor_position = document.translate_row_col_to_index(row, 0)
        self.application.invalidate()

    def _set_status(self, text: str) -> None:
        """更新固定状态区域并管理动画任务。"""
        self.state.set_status(text)
        if self.state.status_text:
            if self._status_task is None or self._status_task.done():
                self._status_task = asyncio.create_task(self._animate_status())
        elif self._status_task is not None:
            self._status_task.cancel()
            self._status_task = None
        self.application.invalidate()

    async def _animate_status(self) -> None:
        """刷新固定状态区域的轻量动画。"""
        while self.state.status_text:
            await asyncio.sleep(0.12)
            self.state.tick_status()
            self.application.invalidate()

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

    def _prompt_fragments(self) -> StyleAndTextTuples:
        """生成输入框提示符。"""
        style = "class:input.prompt.shell" if self.state.shell_mode else "class:input.prompt"
        return [(style, self.prompt_symbol()), ("", " ")]

    def _queue_fragments(self) -> StyleAndTextTuples:
        """生成等待提交队列面板。"""
        items = self.state.queued_messages
        if not items:
            return []

        fragments: StyleAndTextTuples = [
            (
                "class:queue.title",
                "• Messages to be submitted after next tool call "
                "(press esc to interrupt and send immediately)"
            )
        ]
        for item in items[:5]:
            text = " ".join(item.text.split())
            fragments.extend([
                ("", "\n"),
                ("class:queue.arrow", "  ↳ "),
                ("class:queue.text", text)
            ])
        if len(items) > 5:
            fragments.extend([
                ("", "\n"),
                ("class:queue.more", f"    … {len(items) - 5} more")
            ])
        return fragments

    def _composer_hint_fragments(self) -> StyleAndTextTuples:
        """生成输入区的排队提示。"""
        if not self.state.busy or not self.input_buffer.text.strip():
            return []
        completion_state = self.input_buffer.complete_state
        if completion_state is not None and completion_state.completions:
            return []
        return [("class:composer.hint", "  tab to queue message")]

    def _status_fragments(self) -> StyleAndTextTuples:
        """生成固定但默认隐藏的动画状态行。"""
        if self.state.queued_messages or not self.state.status_text:
            return []
        frame = _STATUS_FRAMES[self.state.status_phase % len(_STATUS_FRAMES)]
        return [
            ("class:status.glyph", f"{frame} "),
            ("class:status.text", self.state.status_text)
        ]

    def _footer_fragments(self) -> StyleAndTextTuples:
        """生成固定页脚信息。"""
        return [
            ("class:footer.model", self.model_label),
            ("class:footer.separator", " · "),
            ("class:footer.workspace", self.workspace_label)
        ]


async def run_tui() -> None:
    """运行独立终端界面原型。"""
    await TuiApp().run()


def main() -> None:
    """执行独立终端界面命令入口。"""
    asyncio.run(run_tui())


if __name__ == "__main__":
    main()
