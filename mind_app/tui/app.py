from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEventType

from mind_app.runtime.support.clipboard import ClipboardError, copy_text_to_clipboard
from mind_nova import const

from .approval import ApprovalOverlay
from .events import TuiEvent, simulate_stream
from .scrollbar import ScrollbarMargin, StreamBufferControl
from .state import TuiState, consume_shell_prefix
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
        self._tasks: set[asyncio.Task[Any]] = set()
        self._turn_lock = asyncio.Lock()
        self._status_task: asyncio.Task[Any] | None = None

        self.output_buffer = Buffer(
            document=Document(self.state.transcript, cursor_position=len(self.state.transcript)),
            read_only=True,
            multiline=True
        )
        self.input_buffer = Buffer(
            multiline=True,
            accept_handler=self._accept_input,
            on_text_changed=self._on_input_changed
        )

        self.output_control = StreamBufferControl(
            buffer=self.output_buffer,
            lexer=TranscriptLexer(),
            focusable=True,
            focus_on_click=True,
            on_scroll=self._on_output_scroll
        )
        self.input_control = BufferControl(buffer=self.input_buffer, focusable=True)
        self.output_window = Window(
            self.output_control,
            wrap_lines=True,
            right_margins=[ScrollbarMargin()],
            always_hide_cursor=True
        )

        self.approval = ApprovalOverlay()
        self.key_bindings = self._build_key_bindings()
        self.application: Application[None] = self._build_application()
        self.approval.bind(self.application)

    async def run(self) -> None:
        """启动终端界面并在退出时回收模拟任务。"""
        try:
            await self.application.run_async()
        finally:
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
            f"  Tip: New Build faster with {const.APP_DESC}.\n"
        )

    def _build_application(self) -> Application[None]:
        """创建应用布局和浮层。"""
        prompt = Window(
            FormattedTextControl(self._prompt_fragments),
            width=2,
            dont_extend_width=True,
            always_hide_cursor=True
        )
        input_window = Window(
            self.input_control,
            height=Dimension(min=1, max=6),
            wrap_lines=True,
            style="class:input"
        )
        status_window = Window(
            FormattedTextControl(self._status_fragments),
            height=1,
            always_hide_cursor=True
        )
        footer_window = Window(
            FormattedTextControl(self._footer_fragments),
            height=1,
            align="CENTER",
            always_hide_cursor=True
        )

        body = HSplit([
            self.output_window,
            status_window,
            VSplit([prompt, input_window]),
            Window(height=1),
            footer_window
        ])
        root = FloatContainer(
            content=body,
            floats=[Float(content=self.approval.container)]
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
                self.input_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        @bindings.add("c-o")
        def _newline(event) -> None:
            if event.app.current_buffer is self.input_buffer:
                self.input_buffer.insert_text("\n")

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
            self.state.follow_tail = False
            self.output_window._scroll_up()
            event.app.invalidate()

        @bindings.add("pagedown")
        def _page_down(event) -> None:
            self.output_window._scroll_down()
            self._refresh_follow_tail()
            event.app.invalidate()

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
        self.state.submitted.append(message)
        prefix = "!" if shell_mode else "›"
        self._append_transcript(f"\n{prefix} {message}\n\n")
        self._track_task(asyncio.create_task(self._run_submission(message, shell_mode=shell_mode)))
        return False

    async def _run_submission(self, message: str, *, shell_mode: bool) -> None:
        """顺序消费一轮模拟事件。"""
        async with self._turn_lock:
            async for event in simulate_stream(
                message,
                shell_mode=shell_mode,
                delay=self.stream_delay
            ):
                await self._consume_event(event)

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

    def _on_output_scroll(self, event_type: MouseEventType) -> None:
        """根据滚轮方向更新流式跟随状态。"""
        if event_type == MouseEventType.SCROLL_UP:
            self.state.follow_tail = False
        elif event_type == MouseEventType.SCROLL_DOWN:
            self._refresh_follow_tail(after_down=True)

    def _refresh_follow_tail(self, *, after_down: bool = False) -> None:
        """根据当前窗口位置判断是否恢复末尾跟随。"""
        info = self.output_window.render_info
        if info is None:
            return
        maximum = max(0, info.content_height - info.window_height)
        offset = info.vertical_scroll + (1 if after_down else 0)
        self.state.follow_tail = offset >= maximum

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

    def _track_task(self, task: asyncio.Task[Any]) -> None:
        """保存后台任务并在完成后移除引用。"""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _prompt_fragments(self) -> StyleAndTextTuples:
        """生成输入框提示符。"""
        style = "class:input.prompt.shell" if self.state.shell_mode else "class:input.prompt"
        return [(style, self.prompt_symbol()), ("", " ")]

    def _status_fragments(self) -> StyleAndTextTuples:
        """生成固定但默认隐藏的动画状态行。"""
        if not self.state.status_text:
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
