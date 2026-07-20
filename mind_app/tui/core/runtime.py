# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import shutil
import typing
import asyncio
import contextlib
from prompt_toolkit.application import (
    Application,
    in_terminal
)
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus, to_filter
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import (
    Dimension,
    Layout
)
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    ScrollOffsets,
    VerticalAlign,
    VSplit,
    Window
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.layout.processors import (
    AfterInput,
    ConditionalProcessor
)
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import (
    Style,
    merge_styles
)
from prompt_toolkit.widgets import TextArea
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.interaction.contracts import PromptContext
from .models import FormattedText, FragmentBlock, MenuRequest
from .terminal_input import clear_pending_input
from mind_nova import const
from .activity import TuiActivity
from .approval import TuiApproval
from .approval_render import TUI_APPROVAL_STYLE
from .document import TuiDocument
from .input import INPUT_BUFFER_NAME, TuiInputModel
from .menu import TUI_MENU_STYLE, TuiMenu
from .queued import TuiQueuedMessages, TuiSubmission
from .render import (
    clip_fragments,
    cursor_point,
    cursor_point_for_display_row,
    display_line_count,
    fragments_text,
)
from .styles import query_block

_QUEUE_END = object()
_INPUT_INTERRUPT = object()


class TuiRuntime(object):
    """管理稳定画布上的会话内容、输入队列和审批交互。"""

    INPUT_MAX_LINES: typing.Final[int]     = 8
    QUEUED_MAX_HEIGHT: typing.Final[int]   = 6
    COMPLETION_MAX_HEIGHT: typing.Final[int] = 8

    def __init__(
        self,
        input_model: TuiInputModel | None = None,
        *,
        input_obj: Input | None = None,
        output_obj: Output | None = None,
    ) -> None:
        self.input_model = input_model or TuiInputModel()
        self.context = PromptContext(mode="chat", model="")
        self.placeholder_text = self.input_model.new_placeholder(self.context.mode)

        self.message_queue: asyncio.Queue[typing.Any] = asyncio.Queue()
        self._input_interrupt_pending = False
        self.execution_active = False
        self.queued_messages = TuiQueuedMessages()
        self.input_model.bind_interrupt(self._interrupt_input)
        self.input_model.bind_queue_submission(lambda: self.execution_active)
        self.input_model.bind_queue_rollback(
            lambda: self.execution_active and self.queued_messages.active,
            self._rollback_queued_input,
        )
        self.document = TuiDocument()
        self.status_block: FragmentBlock | None = None
        self._transcript_view_row: int | None = None

        self._application_task: asyncio.Task[None] | None = None
        self._application_error: BaseException | None = None
        self._scrollback_task: asyncio.Task[None] | None = None
        self._open_callbacks: list[typing.Callable[[], None]] = []
        self._closing = False

        self.activity = TuiActivity(
            set_renderable=self.set_status_renderable,
            clear_renderable=self.clear_status_renderable,
        )

        self.input = TextArea(
            name=INPUT_BUFFER_NAME,
            multiline=True,
            lexer=self.input_model.lexer,
            auto_suggest=self.input_model.auto_suggest,
            completer=self.input_model.completer,
            complete_while_typing=Condition(
                lambda: not self.input_model.shell_mode
            ),
            accept_handler=self._accept_input,
            history=self.input_model.history,
            wrap_lines=True,
            height=self._input_dimension,
            dont_extend_height=True,
            get_line_prefix=self._input_line_prefix,
            input_processors=[
                ConditionalProcessor(
                    AfterInput(self._placeholder_fragments),
                    filter=Condition(
                        lambda: not self.input.buffer.text
                        and not self.input_model.shell_mode
                    ),
                )
            ],
        )
        self.input.buffer.enable_history_search = to_filter(True)

        self.transcript_control = FormattedTextControl(
            self._transcript_fragments,
            get_cursor_position=self._transcript_cursor,
        )
        self.status_control = FormattedTextControl(self._status_fragments)
        self.queued_control = FormattedTextControl(self._queued_fragments)
        self.footer_control = FormattedTextControl(self._footer_fragments)

        self.approval = TuiApproval(
            invalidate=self.invalidate,
            focus_card=lambda: self.application.layout.focus(self.approval_control),
            focus_input=lambda: self.application.layout.focus(self.input),
        )
        self.approval_control = FormattedTextControl(
            self.approval.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.approval.key_bindings,
        )
        self.menu = TuiMenu(
            invalidate=self.invalidate,
            focus_menu=lambda: self.application.layout.focus(self.menu_control),
            focus_input=lambda: self.application.layout.focus(self.input),
        )
        self.menu_control = FormattedTextControl(
            self.menu.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.menu.key_bindings,
        )

        self.transcript_window = Window(
            content=self.transcript_control,
            height=self._transcript_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.status_window = Window(
            content=self.status_control,
            height=self._status_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.queued_window = Window(
            content=self.queued_control,
            height=self._queued_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.approval_window = Window(
            content=self.approval_control,
            height=self._approval_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
            style="class:approval-card",
            char=" ",
        )
        self.menu_window = Window(
            content=self.menu_control,
            height=self._menu_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.footer_window = Window(
            content=self.footer_control,
            height=Dimension.exact(1),
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )

        self.approval_card = ConditionalContainer(
            self.approval_window,
            filter=Condition(lambda: self.approval.active),
        )
        self.menu_card = ConditionalContainer(
            VSplit(
                [
                    Window(width=Dimension.exact(2), char=" "),
                    self.menu_window,
                ],
                height=self._menu_dimension,
            ),
            filter=Condition(lambda: self.menu.active),
        )
        self.content_input_gap = ConditionalContainer(
            Window(height=Dimension.exact(1), char=" "),
            filter=Condition(self._has_middle_content),
        )
        self.completion_gap = ConditionalContainer(
            Window(
                height=Dimension.exact(1),
                char=" ",
                dont_extend_height=True,
            ),
            filter=Condition(self._completion_visible),
        )
        self.completion_menu = ConditionalContainer(
            Window(
                content=CompletionsMenuControl(),
                width=Dimension(min=8),
                height=Dimension(min=1, max=self.COMPLETION_MAX_HEIGHT),
                scroll_offsets=ScrollOffsets(top=1, bottom=1),
                dont_extend_width=True,
                dont_extend_height=True,
                style="class:completion-menu",
            ),
            filter=Condition(self._completion_visible),
        )
        self.input_footer = ConditionalContainer(
            HSplit(
                [
                    Window(
                        height=Dimension.exact(1),
                        char=" ",
                        dont_extend_height=True,
                    ),
                    self.footer_window,
                ],
                height=Dimension.exact(2),
            ),
            filter=Condition(self._footer_visible),
        )
        self.input_stack = HSplit(
            [
                self.input,
                self.completion_gap,
                self.completion_menu,
                self.input_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._input_stack_dimension,
        )
        self.input_area = ConditionalContainer(
            self.input_stack,
            filter=Condition(lambda: not self.approval.active),
        )
        self.canvas = HSplit(
            [
                self.transcript_window,
                self.menu_card,
                self.status_window,
                self.queued_window,
                self.content_input_gap,
                self.input_area,
                self.approval_card,
            ],
            align=VerticalAlign.TOP,
            height=self._canvas_dimension,
        )

        dummy_io = (
            input_obj is None
            and output_obj is None
            and not (sys.stdin.isatty() and sys.stdout.isatty())
        )
        application_input = input_obj or (DummyInput() if dummy_io else None)
        application_output = output_obj or (DummyOutput() if dummy_io else None)
        self.application: Application[None] = Application(
            layout=Layout(self.canvas, focused_element=self.input),
            key_bindings=merge_key_bindings([
                self.input_model.key_bindings,
                self._transcript_key_bindings(),
            ]),
            style=self._style(),
            full_screen=False,
            erase_when_done=False,
            mouse_support=False,
            max_render_postpone_time=None,
            input=application_input,
            output=application_output,
        )

    @property
    def active(self) -> bool:
        """返回 TUI 应用是否正在运行。"""
        task = self._application_task
        return task is not None and not task.done()

    @property
    def terminal_width(self) -> int:
        """返回当前渲染输出的终端列数。"""
        size = self._output_size()
        return max(20, size[0])

    @property
    def terminal_height(self) -> int:
        """返回当前渲染输出的终端行数。"""
        size = self._output_size()
        return max(8, size[1])

    async def open(self) -> None:
        """启动持久非全屏输入应用并等待首帧完成。"""
        if self.active:
            return None

        self._closing = False
        self._application_error = None
        previous_render_count = self.application.render_counter
        self._application_task = asyncio.create_task(
            self._run_application(),
            name="mind tui",
        )
        while (
            (
                not self.application.is_running
                or self.application.render_counter == previous_render_count
            )
            and not self._application_task.done()
        ):
            await asyncio.sleep(0)
        for callback in tuple(self._open_callbacks):
            callback()

    def add_open_callback(self, callback: typing.Callable[[], None]) -> None:
        """注册主应用首帧完成后的同步回调。"""
        self._open_callbacks.append(callback)
        if self.active:
            callback()

    async def close(self) -> None:
        """停止输入应用和全部动态任务。"""
        self._closing = True
        await self.end_activity_status()
        await self.approval.close()
        await self.menu.close()
        scrollback_task = self._scrollback_task
        if scrollback_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await scrollback_task
        await self._exit_application(erase=False)

    async def run_modal(
        self,
        factory: typing.Callable[[], typing.Awaitable[typing.Any]],
    ) -> typing.Any:
        """在同一个 Application 任务中暂时让出终端。"""
        context = self.application.context
        if not self.active or context is None:
            return await factory()

        async def invoke() -> typing.Any:
            async with in_terminal(render_cli_done=False):
                return await factory()

        task = context.copy().run(lambda: asyncio.create_task(invoke()))
        return await task

    async def read_message(self, context: PromptContext) -> str:
        """更新输入上下文并按提交顺序读取下一条消息。"""
        self.context = context
        self.placeholder_text = self.input_model.new_placeholder(context.mode)
        self.input_model.set_mode(context.mode)
        self.invalidate()

        queued = self.queued_messages.pop_next()
        submission = queued if queued is not None else await self.message_queue.get()
        self.invalidate()
        if submission is _QUEUE_END:
            if self._application_error is not None:
                raise self._application_error
            raise EOFError
        if submission is _INPUT_INTERRUPT:
            self._input_interrupt_pending = False
            raise KeyboardInterrupt

        if isinstance(submission, TuiSubmission):
            value = submission.value
            visible = submission.visible_text.strip() or value
        else:
            value = str(submission)
            visible = value

        self._transcript_view_row = None
        self.append_gap()
        self.append_block(query_block(visible))
        return value

    async def request_approval(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """在唯一审批区域中读取工具执行决策。"""
        return await self.approval.request(approval)

    async def select_menu(self, request: MenuRequest) -> typing.Any:
        """在主 Application 画布内读取菜单选择。"""
        return await self.menu.request(request)

    def update_menu(self, request: MenuRequest) -> None:
        """更新主画布中的菜单或只读面板。"""
        self.menu.update(request)

    def finish_menu(self, value: typing.Any = None) -> None:
        """结束主画布中的菜单或只读面板。"""
        self.menu.finish(value)

    def append_block(self, block: FragmentBlock) -> None:
        """向会话内容追加一个稳定展示块。"""
        if self.document.append_block(block):
            self.invalidate()
            self._schedule_scrollback_flush()

    def append_gap(self) -> None:
        """请求在下一项正文前保留一个视觉空行。"""
        self.document.request_gap()

    def set_active_renderable(self, block: FragmentBlock) -> None:
        """替换当前流式展示块。"""
        self.document.set_active(block)
        self.invalidate()

    def commit_active_renderable(self, block: FragmentBlock) -> None:
        """把当前动态正文替换为同位置的稳定块。"""
        self.document.commit_active(block)
        self.invalidate()
        self._schedule_scrollback_flush()

    def clear_active_renderable(self) -> None:
        """清空当前流式展示块。"""
        self.document.clear_active()
        self.invalidate()

    def _schedule_scrollback_flush(self) -> None:
        """在稳定正文超过当前视口时安排终端滚屏提交。"""
        task = self._scrollback_task
        if (
            self._closing
            or not self.active
            or self.execution_active
            or self.document.active_block is not None
            or (task is not None and not task.done())
            or self._scrollback_prefix_count() <= 0
        ):
            return None

        context = self.application.context
        if context is None:
            return None
        self._scrollback_task = context.copy().run(
            lambda: asyncio.create_task(
                self._flush_scrollback(),
                name="mind tui scrollback flush",
            )
        )

    async def _flush_scrollback(self) -> None:
        """把超出实时画布的稳定正文提交到终端原生滚屏区。"""
        try:
            while self.active and not self.execution_active:
                count = self._scrollback_prefix_count()
                if count <= 0:
                    return None

                fragments = self.document.stable_prefix_fragments(count)
                retained = self.document.blocks[count:]
                separator = (
                    "\n\n"
                    if not retained or retained[0].gap_before
                    else "\n"
                )
                async with in_terminal(render_cli_done=False):
                    self.application.print_text([
                        *fragments,
                        ("", separator),
                    ])
                    self.document.discard_stable_prefix(count)
                    self._transcript_view_row = None
        finally:
            self._scrollback_task = None
            self.invalidate()

    def _scrollback_prefix_count(self) -> int:
        """计算应提交到终端滚屏区的稳定正文块数量。"""
        if self.document.active_block is not None:
            return 0
        blocks = self.document.blocks
        if not blocks:
            return 0

        available = self._transcript_available_height()
        kept_rows = 0
        first_kept = len(blocks)
        for index in range(len(blocks) - 1, -1, -1):
            text = fragments_text(blocks[index].block.fragments).strip("\r\n")
            block_rows = display_line_count(text, width=self.terminal_width)
            separator_rows = (
                1 if first_kept < len(blocks) and blocks[first_kept].gap_before
                else 0
            )
            candidate = block_rows + separator_rows + kept_rows
            if candidate > available:
                break
            kept_rows = candidate
            first_kept = index

        return first_kept

    def set_status_renderable(self, block: FragmentBlock) -> None:
        """替换动画专属区域内容。"""
        self.status_block = block
        self.invalidate()

    def clear_status_renderable(self) -> None:
        """清空动画专属区域内容。"""
        self.status_block = None
        self.invalidate()

    def set_execution_active(self, active: bool) -> None:
        """更新模型轮次执行状态并切换输入区布局。"""
        self.execution_active = bool(active)
        self.invalidate()
        if not self.execution_active:
            self._schedule_scrollback_flush()

    async def begin_mode_status(self, mode: str) -> None:
        """启动模式等待动画。"""
        await self.activity.begin_mode(mode)

    async def begin_upload_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动附件上传动画。"""
        await self.activity.begin_upload(snapshot)

    async def begin_inbuild_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动内置运行时状态动画。"""
        await self.activity.begin_inbuild(snapshot)

    async def begin_external_mcp_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动外部 MCP 状态动画。"""
        await self.activity.begin_external_mcp(snapshot)

    async def end_activity_status(self) -> None:
        """结束运行期活动动画。"""
        await self.activity.stop()

    def invalidate(self) -> None:
        """请求重新绘制当前稳定画布。"""
        if self.active and not self.application.is_done:
            with contextlib.suppress(Exception):
                self.application.invalidate()

    async def _run_application(self) -> None:
        """运行输入应用并传播终端结束状态。"""
        try:
            with create_app_session(
                input=self.application.input,
                output=self.application.output,
            ):
                with patch_stdout(raw=True):
                    await self.application.run_async(
                        pre_run=lambda: clear_pending_input(self.application.input)
                    )
        except (EOFError, KeyboardInterrupt) as exc:
            self._application_error = exc
        except BaseException as exc:
            self._application_error = exc
        finally:
            if not self._closing:
                self.message_queue.put_nowait(_QUEUE_END)

    async def _exit_application(self, *, erase: bool) -> None:
        """结束当前应用任务并按需清除画布。"""
        task = self._application_task
        if task is None:
            return None

        self.application.erase_when_done = erase
        if not self.application.is_done:
            with contextlib.suppress(Exception):
                self.application.exit(result=None)
        with contextlib.suppress(asyncio.CancelledError, EOFError):
            await task
        self._application_task = None
        self.application.erase_when_done = False

    def _accept_input(self, buffer) -> bool:
        """恢复折叠粘贴内容并把输入追加到队列。"""
        editable_text = buffer.text
        paste_store = self.input_model.submission_state()
        shell_mode = self.input_model.shell_mode
        value = self.input_model.restore_submission(buffer.text)
        if not value and not shell_mode:
            self.input_model.clear_submission_state()
            self.invalidate()
            return False
        if shell_mode:
            value = f"! {value}" if value else "!"
        submission = TuiSubmission(
            value=value,
            editable_text=editable_text,
            paste_store=paste_store,
            shell_mode=shell_mode,
        )
        if self.execution_active:
            self.queued_messages.append(submission)
        else:
            self.message_queue.put_nowait(submission)
        buffer.text = submission.visible_text
        buffer.cursor_position = len(buffer.text)
        self.input_model.clear_submission_state()
        self.invalidate()
        return False

    def _rollback_queued_input(self) -> bool:
        """撤回最近一条待提交消息并恢复到主输入框。"""
        item = self.queued_messages.pop_last()
        if item is None:
            return False
        self.input_model.rollback_submission_history(item.visible_text)
        self.input.buffer.text = item.editable_text
        self.input.buffer.cursor_position = len(item.editable_text)
        self.input_model.restore_submission_state(item.paste_store)
        self.input_model.set_shell_mode(item.shell_mode)
        self.invalidate()
        return True

    def _interrupt_input(self) -> None:
        """把输入区中断交给会话读取循环处理。"""
        if self._input_interrupt_pending:
            return None
        self._input_interrupt_pending = True
        self.message_queue.put_nowait(_INPUT_INTERRUPT)
        self.invalidate()

    def _input_line_prefix(
        self,
        line_number: int,
        wrap_count: int,
    ) -> StyleAndTextTuples:
        """生成输入首行和续行的无边框前缀。"""
        if line_number == 0 and wrap_count == 0:
            marker = "! " if self.input_model.shell_mode else "> "
            return [("class:prompt.kicker", marker)]
        return [("class:prompt.kicker", ". ")]

    def _placeholder_fragments(self) -> StyleAndTextTuples:
        """返回当前输入轮次固定的占位文案。"""
        return [("class:placeholder", f" {self.placeholder_text}")]

    def _transcript_fragments(self) -> FormattedText:
        """生成会话内容区域的格式化片段。"""
        return self.document.fragments(width=self.terminal_width)

    def _status_fragments(self) -> FormattedText:
        """生成动画专属区域的格式化片段。"""
        if self.status_block is None:
            return []
        return list(self.status_block.fragments)

    def _queued_fragments(self) -> FormattedText:
        """生成动画区域下方的待提交消息。"""
        return self.queued_messages.fragments(width=self.terminal_width)

    def _footer_fragments(self) -> FormattedText:
        """生成单行 TUI 信息栏。"""
        theme = self.input_model.theme(self.context.mode)
        parts: FormattedText = [(f"fg:{theme['brand']} bold", const.APP_DESC)]
        values = [
            ("class:prompt.model", self.context.model or "-"),
            ("class:prompt.access", self.context.access_label),
            ("class:prompt.workspace", self.context.workspace_label),
        ]
        if self.context.exec_status_label:
            values.append(("class:prompt.exec.command", self.context.exec_status_label))
        for style, value in values:
            text = str(value or "").strip()
            if text:
                parts.extend([
                    ("class:prompt.kicker", " · "),
                    (style, text),
                ])
        return clip_fragments(parts, width=self.terminal_width)

    def _transcript_cursor(self) -> Point:
        """让会话内容视口跟随最新输出。"""
        text = fragments_text(self._transcript_fragments())
        if self._transcript_view_row is None:
            x, y = cursor_point(text, width=self.terminal_width)
        else:
            x, y = cursor_point_for_display_row(
                text,
                width=self.terminal_width,
                display_row=self._transcript_view_row,
            )
        return Point(x=x, y=y)

    def _transcript_key_bindings(self) -> KeyBindings:
        """创建正文视口翻页按键。"""
        bindings = KeyBindings()
        input_active = has_focus(INPUT_BUFFER_NAME)

        @bindings.add("pageup", eager=True, filter=input_active)
        def _(event) -> None:
            _ = event
            self._scroll_transcript_page(-1)

        @bindings.add("pagedown", eager=True, filter=input_active)
        def _(event) -> None:
            _ = event
            self._scroll_transcript_page(1)

        return bindings

    def _scroll_transcript_page(self, direction: int) -> None:
        """按当前正文窗口高度向前或向后翻页。"""
        text = fragments_text(self._transcript_fragments())
        if not text:
            return None

        total_rows = display_line_count(text, width=self.terminal_width)
        render_info = self.transcript_window.render_info
        window_height = (
            render_info.window_height
            if render_info is not None
            else self._transcript_dimension().preferred
        )
        if total_rows <= window_height:
            self._transcript_view_row = None
            return None

        last_row = max(0, total_rows - 1)
        current_row = (
            last_row
            if self._transcript_view_row is None
            else min(self._transcript_view_row, last_row)
        )
        page_rows = max(1, window_height - 1)
        target_row = max(0, min(last_row, current_row + direction * page_rows))
        self._transcript_view_row = None if target_row >= last_row else target_row
        self.invalidate()

    def _canvas_dimension(self) -> Dimension:
        """返回随可见内容增长并受终端高度限制的画布高度。"""
        return Dimension.exact(self._visible_height())

    def _transcript_dimension(self) -> Dimension:
        """按剩余画布空间限制会话内容高度。"""
        text = fragments_text(self._transcript_fragments())
        rows = display_line_count(text, width=self.terminal_width)
        return Dimension.exact(min(rows, self._transcript_available_height()))

    def _transcript_available_height(self) -> int:
        """返回当前交互区域之外可分配给正文的终端行数。"""
        return max(
            0,
            self.terminal_height
            - self._interaction_height()
            - self._status_height()
            - self._queued_height()
            - self._menu_height()
            - int(self._has_middle_content()),
        )

    def _status_dimension(self) -> Dimension:
        """返回动画区域的精确高度。"""
        return Dimension.exact(self._status_height())

    def _queued_dimension(self) -> Dimension:
        """返回待提交消息区域的精确高度。"""
        return Dimension.exact(self._queued_height())

    def _input_dimension(self) -> Dimension:
        """返回输入框当前显示高度。"""
        return Dimension.exact(self._input_height())

    def _input_stack_dimension(self) -> Dimension:
        """返回输入框、补全列表和当前可见 footer 的总高度。"""
        return Dimension.exact(self._input_stack_height())

    def _approval_dimension(self) -> Dimension:
        """返回审批卡当前显示高度。"""
        return Dimension.exact(self._approval_height())

    def _menu_dimension(self) -> Dimension:
        """返回内嵌菜单当前显示高度。"""
        return Dimension.exact(self._menu_height())

    def _status_height(self) -> int:
        """计算动画区域占用行数。"""
        text = fragments_text(self._status_fragments())
        if not text:
            return 0
        return min(3, max(1, display_line_count(text, width=self.terminal_width)))

    def _queued_height(self) -> int:
        """计算待提交消息区域占用行数。"""
        text = fragments_text(self._queued_fragments())
        if not text:
            return 0
        rows = display_line_count(text, width=self.terminal_width)
        return min(self.QUEUED_MAX_HEIGHT, max(1, rows))

    def _footer_height(self) -> int:
        """返回当前输入区 footer 及其间距占用高度。"""
        return 2 if self._footer_visible() else 0

    def _footer_visible(self) -> bool:
        """判断输入框下方的信息栏是否应当显示。"""
        return bool(
            not self.queued_messages.active
            and not self._overlay_active()
        )

    def _overlay_active(self) -> bool:
        """判断补全、选择菜单或审批层是否正在显示。"""
        return bool(
            self.approval.active
            or self.menu.active
            or self._completion_visible()
        )

    def _completion_visible(self) -> bool:
        """判断输入框是否存在可展示的补全候选项。"""
        state = self.input.buffer.complete_state
        return bool(
            not self.approval.active
            and not self.menu.active
            and state is not None
            and state.completions
        )

    def _completion_height(self) -> int:
        """计算无边框补全列表占用行数。"""
        if not self._completion_visible():
            return 0
        state = self.input.buffer.complete_state
        count = len(state.completions) if state is not None else 0
        available = max(1, self.terminal_height - self._input_height() - 1)
        return min(self.COMPLETION_MAX_HEIGHT, count, available)

    def _completion_section_height(self) -> int:
        """返回补全列表及其顶部间距的总高度。"""
        height = self._completion_height()
        return height + 1 if height else 0

    def _input_stack_height(self) -> int:
        """返回当前完整输入区域占用高度。"""
        return (
            self._input_height()
            + self._completion_section_height()
            + self._footer_height()
        )

    def _input_height(self) -> int:
        """计算输入内容占用的显示行数。"""
        rows = display_line_count(
            self.input.buffer.text,
            width=max(1, self.terminal_width - 2),
        )
        return max(1, min(self.INPUT_MAX_LINES, rows))

    def _approval_height(self) -> int:
        """计算审批卡在当前画布中的显示高度。"""
        if not self.approval.active:
            return 0
        text = fragments_text(self.approval.fragments())
        rows = display_line_count(text, width=self.terminal_width)
        available = max(
            1,
            self.terminal_height
            - self._status_height()
            - self._queued_height()
            - self._menu_height()
            - int(self._has_middle_content()),
        )
        return min(rows, available)

    def _menu_height(self) -> int:
        """计算内嵌菜单在当前画布中的显示高度。"""
        if not self.menu.active:
            return 0
        available = max(
            1,
            self.terminal_height
            - self._input_stack_height()
            - self._status_height()
            - self._queued_height()
            - 1,
        )
        return min(self.menu.height(), available)

    def _interaction_height(self) -> int:
        """返回输入区或审批区当前占用的高度。"""
        if self.approval.active:
            return self._approval_height()
        return self._input_stack_height()

    def _visible_height(self) -> int:
        """计算当前画布实际可见内容的高度。"""
        height = (
            self._transcript_dimension().preferred
            + self._status_height()
            + self._queued_height()
            + self._menu_height()
            + int(self._has_middle_content())
            + self._interaction_height()
        )
        return max(1, min(self.terminal_height, height))

    def _has_middle_content(self) -> bool:
        """判断输入框上方是否存在可见内容。"""
        return bool(
            self.document.has_content
            or self.status_block is not None
            or self.queued_messages.active
            or self.menu.active
        )

    def _output_size(self) -> tuple[int, int]:
        """读取应用输出尺寸并提供标准终端回退值。"""
        application = getattr(self, "application", None)
        if application is not None:
            with contextlib.suppress(Exception):
                size = application.output.get_size()
                return int(size.columns), int(size.rows)
        fallback = shutil.get_terminal_size(fallback=(100, 24))
        return fallback.columns, fallback.lines

    def _style(self):
        """创建 TUI 交互区域使用的组合样式。"""
        base = self.input_model.style
        overrides = Style.from_dict({
            "auto-suggestion": "bg:default #5A616A",
            "completion-menu": "bg:default #D8DCE2",
            "completion-menu.completion": "bg:default bold #D6DBE2",
            "completion-menu.completion.current": "bg:default bold #F4F7FA",
            "completion-menu.meta.completion": "bg:default #7D858F",
            "completion-menu.meta.completion.current": "bg:default #AFC7D8",
            "queue.label": "bg:default #8A929C bold",
            "queue.marker": "bg:default #7B838E bold",
            "queue.text": "bg:default #DDE7EF",
        })
        return merge_styles([
            base,
            TUI_APPROVAL_STYLE,
            TUI_MENU_STYLE,
            overrides,
        ])


if __name__ == '__main__':
    pass
