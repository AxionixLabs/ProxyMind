# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import shutil
import typing
import contextlib
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import (
    Condition,
    has_focus,
    to_filter
)
from prompt_toolkit.formatted_text import (
    FormattedText as PromptFormattedText,
    StyleAndTextTuples,
)
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import (
    KeyBindings,
    merge_key_bindings
)
from prompt_toolkit.layout import (
    Dimension,
    Layout
)
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    VerticalAlign,
    VSplit,
    Window
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (
    AfterInput,
    ConditionalProcessor
)
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.widgets import TextArea
from mind_app.interaction.contracts import PromptContext
from mind_nova import const
from .approval import TuiApproval
from .approval_render import TUI_APPROVAL_STYLE
from .bottom_pane import (
    BottomSurface,
    TuiBottomPane
)
from .document import TuiDocument
from .input import (
    INPUT_BUFFER_NAME,
    TuiInputModel
)
from .interrupt import TuiInterruptState
from .menu import (
    TUI_MENU_STYLE,
    TuiMenu
)
from .models import (
    FormattedText,
    FragmentBlock
)
from .process_status import TuiProcessStatus
from .process_viewer import TuiProcessViewer
from .queued import TuiQueuedMessages
from .render import (
    clip_fragments,
    cursor_point,
    cursor_point_for_display_row,
    display_line_count,
    fragment_continuation_widths,
    fragments_text
)
from .styles import (
    ASSISTANT_PREFIX_CLASS,
    build_tui_application_style,
    exit_summary_fragments
)


class TuiScreen(object):
    """持有单一 Application、视觉组件和布局尺寸策略。"""

    INPUT_MAX_LINES: typing.Final[int]               = 8
    QUEUED_MAX_HEIGHT: typing.Final[int]             = 6
    COMPLETION_MAX_HEIGHT: typing.Final[int]         = 8
    CONTENT_INPUT_GAP_HEIGHT: typing.Final[int]      = 2
    OVERLAY_INPUT_GAP_HEIGHT: typing.Final[int]      = 1
    COMMAND_SURFACE_GAP_HEIGHT: typing.Final[int]    = 2
    FOOTER_GAP_HEIGHT: typing.Final[int]             = 1
    ESCAPE_SEQUENCE_TIMEOUT_SEC: typing.Final[float] = 0.1

    def __init__(
        self,
        *,
        input_model: TuiInputModel,
        document: TuiDocument,
        queued_messages: TuiQueuedMessages,
        interrupt_state: TuiInterruptState,
        get_context: typing.Callable[[], PromptContext],
        get_placeholder_text: typing.Callable[[], str],
        get_submission_deferred: typing.Callable[[], bool],
        get_queued_submission_text: typing.Callable[[], str | None],
        get_surface_submission_pending: typing.Callable[[], bool],
        get_transcript_view_row: typing.Callable[[], int | None],
        accept_input: typing.Callable[[Buffer], bool],
        on_input_text_changed: typing.Callable[[Buffer], None],
        clear_exit_confirmation: typing.Callable[[], None],
        clear_visible_transcript: typing.Callable[[], None],
        scroll_transcript_page: typing.Callable[[int], None],
        input_obj: Input | None = None,
        output_obj: Output | None = None
    ) -> None:
        self.input_model     = input_model
        self.document        = document
        self.queued_messages = queued_messages
        self.interrupt_state = interrupt_state

        self._get_context                    = get_context
        self._get_placeholder_text           = get_placeholder_text
        self._get_submission_deferred        = get_submission_deferred
        self._get_queued_submission_text     = get_queued_submission_text
        self._get_surface_submission_pending = get_surface_submission_pending
        self._get_transcript_view_row        = get_transcript_view_row

        self._clear_exit_confirmation  = clear_exit_confirmation
        self._clear_visible_transcript = clear_visible_transcript

        self._scroll_transcript_page = scroll_transcript_page

        self.activity_block: FragmentBlock | None = None

        self.process_status = TuiProcessStatus(
            invalidate=self.invalidate,
            get_width=lambda: self.terminal_width,
        )

        self.input = TextArea(
            name=INPUT_BUFFER_NAME,
            style="class:input-surface",
            multiline=True,
            lexer=self.input_model.lexer,
            auto_suggest=self.input_model.auto_suggest,
            completer=self.input_model.completer,
            complete_while_typing=Condition(
                lambda: not self.input_model.shell_mode
            ),
            accept_handler=accept_input,
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
        self.input.buffer.on_text_changed += on_input_text_changed

        self.transcript_control = FormattedTextControl(
            self._transcript_fragments,
            get_cursor_position=self._transcript_cursor,
        )

        self.status_control = FormattedTextControl(self._status_fragments)

        self.process_status_control = FormattedTextControl(
            self.process_status.fragments
        )

        self.queued_control = FormattedTextControl(self._queued_fragments)
        self.footer_control = FormattedTextControl(self._footer_fragments)

        self.bottom_pane = TuiBottomPane(
            focus_surface=self._focus_bottom_surface,
            focus_input=lambda: self.application.layout.focus(self.input),
            invalidate=self.invalidate,
        )

        self.approval = TuiApproval(
            invalidate=self.invalidate,
            focus_card=lambda: self.bottom_pane.activate("approval"),
            focus_input=lambda: self.bottom_pane.deactivate("approval"),
            get_width=lambda: self.terminal_width,
            get_max_height=self._approval_available_height,
        )
        self.approval_control = FormattedTextControl(
            self.approval.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.approval.key_bindings,
        )
        self.menu = TuiMenu(
            invalidate=self.invalidate,
            focus_menu=lambda: self.bottom_pane.activate("menu"),
            focus_input=lambda: self.bottom_pane.deactivate("menu"),
            get_width=lambda: self.terminal_width,
        )
        self.menu_control = FormattedTextControl(
            self.menu.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.menu.key_bindings,
        )
        self.process_viewer = TuiProcessViewer(
            invalidate=self.invalidate,
            focus_viewer=lambda: self.bottom_pane.activate("process_viewer"),
            focus_input=lambda: self.bottom_pane.deactivate("process_viewer"),
            get_width=lambda: self.terminal_width,
        )
        self.process_viewer_control = FormattedTextControl(
            self.process_viewer.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.process_viewer.key_bindings,
        )

        self.transcript_window = Window(
            content=self.transcript_control,
            height=self._transcript_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
            get_line_prefix=self._transcript_line_prefix,
        )
        self.status_window = Window(
            content=self.status_control,
            height=self._status_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.process_status_window = Window(
            content=self.process_status_control,
            height=self._process_status_dimension,
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
            wrap_lines=False,
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
        self.process_viewer_window = Window(
            content=self.process_viewer_control,
            height=self._process_viewer_dimension,
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
            filter=Condition(lambda: self.bottom_pane.is_active("approval")),
        )
        self.menu_card = ConditionalContainer(
            self.menu_window,
            filter=Condition(lambda: self.bottom_pane.is_active("menu")),
        )
        self.process_viewer_card = ConditionalContainer(
            self.process_viewer_window,
            filter=Condition(
                lambda: self.bottom_pane.is_active("process_viewer")
            ),
        )
        self.transcript_status_gap = ConditionalContainer(
            Window(height=Dimension.exact(1), char=" "),
            filter=Condition(self._transcript_status_gap_visible),
        )
        self.content_input_gap = ConditionalContainer(
            Window(height=self._content_input_gap_dimension, char=" "),
            filter=Condition(self._content_input_gap_visible),
        )
        self.completion_gap = ConditionalContainer(
            Window(
                height=Dimension.exact(1),
                char=" ",
                dont_extend_height=True,
            ),
            filter=Condition(self._completion_visible),
        )
        self.completion_menu = CompletionsMenu(
            max_height=self.COMPLETION_MAX_HEIGHT,
            scroll_offset=1,
            extra_filter=Condition(self._completion_visible),
        )

        typing.cast(Window, self.completion_menu.content).right_margins.clear()

        self.completion_menu_row = ConditionalContainer(
            VSplit([
                Window(
                    width=Dimension.exact(1),
                    char=" ",
                    dont_extend_width=True,
                ),
                self.completion_menu,
            ]),
            filter=Condition(self._completion_visible),
        )

        self.input_footer = ConditionalContainer(
            HSplit(
                [
                    Window(
                        height=Dimension.exact(self.FOOTER_GAP_HEIGHT),
                        char=" ",
                        dont_extend_height=True,
                    ),
                    self.footer_window,
                ],
                height=Dimension.exact(self.FOOTER_GAP_HEIGHT + 1),
                window_too_small=Window(),
            ),
            filter=Condition(self._footer_visible),
        )

        self.input_stack = HSplit(
            [
                self.input,
                self.completion_gap,
                self.completion_menu_row,
                self.input_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._input_stack_dimension,
            window_too_small=Window(),
        )
        self.input_area = ConditionalContainer(
            self.input_stack,
            filter=Condition(lambda: self.bottom_pane.input_visible),
        )

        self.canvas = HSplit(
            [
                self.transcript_window,
                self.transcript_status_gap,
                self.status_window,
                self.process_status_window,
                self.queued_window,
                self.content_input_gap,
                self.process_viewer_card,
                self.menu_card,
                self.input_area,
                self.approval_card,
            ],
            align=VerticalAlign.TOP,
            height=self._canvas_dimension,
            window_too_small=Window(),
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
            style=build_tui_application_style(
                self.input_model.style,
                TUI_APPROVAL_STYLE,
                TUI_MENU_STYLE,
            ),
            full_screen=False,
            erase_when_done=False,
            mouse_support=False,
            max_render_postpone_time=None,
            input=application_input,
            output=application_output,
        )
        self.application.ttimeoutlen = self.ESCAPE_SEQUENCE_TIMEOUT_SEC

    @property
    def terminal_width(self) -> int:
        """返回当前渲染输出的终端列数。"""
        return max(20, self._output_size()[0])

    @property
    def terminal_height(self) -> int:
        """返回当前渲染输出的终端行数。"""
        return max(1, self._output_size()[1])

    @staticmethod
    def _transcript_continuation_widths(
        fragments: FormattedText,
    ) -> tuple[int, ...]:
        """返回正文每个逻辑行的自动折行前缀宽度。"""
        return fragment_continuation_widths(
            fragments,
            prefix_style=ASSISTANT_PREFIX_CLASS,
            prefix_width=2,
        )

    def invalidate(self) -> None:
        """请求重新绘制当前稳定画布。"""
        application = getattr(self, "application", None)
        if (
            application is not None
            and application.is_running
            and not application.is_done
        ):
            with contextlib.suppress(Exception):
                application.invalidate()

    def set_activity_renderable(self, block: FragmentBlock) -> None:
        """替换活动状态区域的展示内容。"""
        self.activity_block = block
        self.invalidate()

    def clear_activity_renderable(self) -> None:
        """清空活动状态区域的展示内容。"""
        self.activity_block = None
        self.invalidate()

    def clear_terminal_scrollback(self) -> None:
        """清除当前画布及终端滚屏缓冲区。"""
        self.application.renderer.clear()
        _erase_terminal_scrollback(self.application.output)
        self.invalidate()

    def print_exit_summary(self) -> None:
        """在 Application 停止后向终端打印静态退出摘要。"""
        with contextlib.suppress(EOFError, OSError, ValueError):
            print_formatted_text(
                PromptFormattedText(
                    (
                        ("", "\n"),
                        *exit_summary_fragments(),
                    ),
                ),
                output=self.application.output,
                include_default_pygments_style=False,
            )

    def transcript_fragments(self) -> FormattedText:
        """生成会话内容区域的格式化片段。"""
        return self.document.fragments(width=self.terminal_width)

    def transcript_available_height(self) -> int:
        """估算首帧渲染前正文可使用的终端行数。"""
        return max(
            0,
            self.terminal_height
            - self._interaction_height()
            - self._status_height()
            - self._process_status_height()
            - self._queued_height()
            - self._menu_height()
            - self._process_viewer_height()
            - int(self._transcript_status_gap_visible())
            - self._content_input_gap_height(),
        )

    def _focus_bottom_surface(self, surface: BottomSurface) -> None:
        """把焦点切换到指定的底部临时交互表面。"""
        self._clear_exit_confirmation()
        controls = {
            "approval": self.approval_control,
            "menu": self.menu_control,
            "process_viewer": self.process_viewer_control,
        }
        self.application.layout.focus(controls[surface])

    def _input_line_prefix(
        self,
        line_number: int,
        wrap_count: int,
    ) -> StyleAndTextTuples:
        """生成输入首行和续行的无边框前缀。"""
        if line_number == 0 and wrap_count == 0:
            if self.input_model.shell_mode:
                return [("class:shell-escape", "! ")]
            return [("class:prompt.kicker", "› ")]
        return [("class:prompt.kicker", ". ")]

    def _transcript_line_prefix(
        self,
        line_number: int,
        wrap_count: int
    ) -> StyleAndTextTuples:
        """让助手正文自动折行后继续与首行正文对齐。"""
        if wrap_count <= 0 or not self._assistant_line(line_number):
            return []
        return [(ASSISTANT_PREFIX_CLASS, "  ")]

    def _transcript_fragments(self) -> FormattedText:
        """返回正文控件使用的格式化片段。"""
        return self.transcript_fragments()

    def _placeholder_fragments(self) -> StyleAndTextTuples:
        """返回当前输入轮次固定的占位文案。"""
        return [("class:placeholder", self._get_placeholder_text())]

    def _assistant_line(self, target_line: int) -> bool:
        """判断指定正文逻辑行是否属于助手正文块。"""
        line_number   = 0
        at_line_start = True

        for style, text in self.transcript_fragments():
            parts = text.split("\n")
            last_index = len(parts) - 1
            for index, part in enumerate(parts):
                if line_number == target_line and at_line_start and part:
                    return style == ASSISTANT_PREFIX_CLASS
                if part:
                    at_line_start = False
                if index < last_index:
                    if line_number == target_line:
                        return False
                    line_number += 1
                    at_line_start = True

        return False

    def _status_fragments(self) -> FormattedText:
        """生成动画专属区域的格式化片段。"""
        block = self.activity_block
        return list(block.fragments) if block is not None else []

    def _queued_fragments(self) -> FormattedText:
        """生成动画区域下方的待提交消息。"""
        return self.queued_messages.fragments(
            width=self.terminal_width,
            max_rows=self.QUEUED_MAX_HEIGHT,
        )

    def _footer_fragments(self) -> FormattedText:
        """生成单行 TUI 信息栏。"""
        if self.interrupt_state.exit_armed:
            return [
                ("class:footer.exit-key", "Ctrl + C"),
                ("class:footer.exit-hint", " again to exit"),
            ]
        if self._queue_submission_hint_visible():
            return [("class:footer.queue-hint", "  tab to queue message")]
        if (
            self.document.has_pending_submission
            or self._get_surface_submission_pending()
        ):
            return []

        context = self._get_context()
        theme   = self.input_model.theme(context.mode)

        parts: FormattedText = [(f"fg:{theme['brand']}", const.APP_DESC)]

        access_label = str(context.access_label or "").strip()

        access_style = (
            "class:footer.access.full"
            if access_label.lower() == "elevated"
            else "class:footer.access"
        )
        values = [
            ("class:footer.model", context.model or "-"),
            (access_style, access_label),
            ("class:footer.workspace", context.workspace_label),
        ]
        for style, value in values:
            text = str(value or "").strip()
            if text:
                parts.extend([
                    ("class:footer.separator", " · "),
                    (style, text),
                ])
        return clip_fragments(parts, width=self.terminal_width)

    def _transcript_cursor(self) -> Point:
        """让会话内容视口跟随最新输出。"""
        fragments = self.transcript_fragments()
        text      = fragments_text(fragments)
        view_row  = self._get_transcript_view_row()

        if view_row is None:
            x, y = cursor_point(text, width=self.terminal_width)
        else:
            x, y = cursor_point_for_display_row(
                text,
                width=self.terminal_width,
                display_row=view_row,
                continuation_widths=self._transcript_continuation_widths(
                    fragments,
                ),
            )

        return Point(x=x, y=y)

    def _transcript_key_bindings(self) -> KeyBindings:
        """创建正文视口翻页按键。"""
        bindings     = KeyBindings()
        input_active = has_focus(INPUT_BUFFER_NAME)

        @bindings.add("c-l", eager=True, filter=input_active)
        def _(event) -> None:
            _ = event
            self._clear_visible_transcript()

        @bindings.add("pageup", eager=True, filter=input_active)
        def _(event) -> None:
            _ = event
            self._scroll_transcript_page(-1)

        @bindings.add("pagedown", eager=True, filter=input_active)
        def _(event) -> None:
            _ = event
            self._scroll_transcript_page(1)

        return bindings

    def _canvas_dimension(self) -> Dimension:
        """返回随内容自然增长并受终端高度限制的画布高度。"""
        return Dimension.exact(self._visible_height())

    def _transcript_dimension(self) -> Dimension:
        """返回正文当前内容在画布中占用的高度。"""
        fragments = self.transcript_fragments()
        text      = fragments_text(fragments)

        rows = display_line_count(
            text,
            width=self.terminal_width,
            continuation_widths=self._transcript_continuation_widths(
                fragments,
            ),
        )
        return Dimension.exact(min(rows, self.transcript_available_height()))

    def _status_dimension(self) -> Dimension:
        """返回动画区域的精确高度。"""
        return Dimension.exact(self._status_height())

    def _queued_dimension(self) -> Dimension:
        """返回待提交消息区域的精确高度。"""
        return Dimension.exact(self._queued_height())

    def _process_status_dimension(self) -> Dimension:
        """返回后台进程状态区域的精确高度。"""
        return Dimension.exact(self._process_status_height())

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

    def _process_viewer_dimension(self) -> Dimension:
        """返回进程查看器当前显示高度。"""
        return Dimension.exact(self._process_viewer_height())

    def _status_height(self) -> int:
        """计算动画区域占用行数。"""
        if self.bottom_pane.is_active("approval"):
            return 0
        text = fragments_text(self._status_fragments())
        if not text:
            return 0
        return min(5, max(1, display_line_count(text, width=self.terminal_width)))

    def _queued_height(self) -> int:
        """计算待提交消息区域占用行数。"""
        if self.bottom_pane.is_active("approval"):
            return 0
        text = fragments_text(self._queued_fragments())
        if not text:
            return 0
        rows = display_line_count(text, width=self.terminal_width)
        return min(self.QUEUED_MAX_HEIGHT, max(1, rows))

    def _process_status_height(self) -> int:
        """计算后台进程状态区域占用行数。"""
        if self.bottom_pane.transient_active:
            return 0
        return 1 if self.process_status.active else 0

    def _footer_height(self) -> int:
        """返回当前输入区 footer 及其间距占用高度。"""
        return self.FOOTER_GAP_HEIGHT + 1 if self._footer_visible() else 0

    def _footer_visible(self) -> bool:
        """判断输入框下方的信息栏是否应当显示。"""
        return not self._overlay_active()

    def _queue_submission_hint_visible(self) -> bool:
        """判断执行期间是否应显示输入排队提示。"""
        return bool(
            self._get_submission_deferred()
            and self.input.buffer.text != self._get_queued_submission_text()
            and (self.input.buffer.text.strip() or self.input_model.shell_mode)
        )

    def _queued_content_visible(self) -> bool:
        """判断待提交区域是否存在消息。"""
        return self.queued_messages.active

    def _overlay_active(self) -> bool:
        """判断补全、选择菜单或审批层是否正在显示。"""
        return bool(self.bottom_pane.transient_active or self._completion_visible())

    def _completion_visible(self) -> bool:
        """判断输入框是否存在可展示的补全候选项。"""
        state = self.input.buffer.complete_state
        return bool(
            self.bottom_pane.input_visible
            and (
                (state is not None and state.completions)
                or self._expected_completion_count()
            )
        )

    def _expected_completion_count(self) -> int:
        """同步计算当前输入应展示的补全项数量。"""
        if not self.bottom_pane.input_visible:
            return 0
        return len(self.input_model.completer.matching_completions(
            self.input.buffer.document
        ))

    def _completion_height(self) -> int:
        """计算无边框补全列表占用行数。"""
        if not self._completion_visible():
            return 0
        state = self.input.buffer.complete_state
        loaded_count = len(state.completions) if state is not None else 0
        count = max(loaded_count, self._expected_completion_count())
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
        text = self.input.buffer.text
        rows = display_line_count(text, width=max(1, self.terminal_width - 2))
        if text.endswith("\n"):
            rows += 1
        return max(1, min(self.INPUT_MAX_LINES, rows))

    def _approval_height(self) -> int:
        """计算审批卡在当前画布中的显示高度。"""
        if not self.bottom_pane.is_active("approval"):
            return 0
        text = fragments_text(self.approval.fragments())
        rows = display_line_count(text, width=self.terminal_width)
        return min(rows, self._approval_available_height())

    def _approval_available_height(self) -> int:
        """返回审批内容在当前终端中的可用高度。"""
        return max(
            1,
            self.terminal_height
            - self._status_height()
            - self._queued_height()
            - self._menu_height()
            - self._process_viewer_height()
            - int(self._transcript_status_gap_visible())
            - self._content_input_gap_height(),
        )

    def _menu_height(self) -> int:
        """计算内嵌菜单在当前画布中的显示高度。"""
        if not self.bottom_pane.is_active("menu"):
            return 0
        available = max(
            1,
            self.terminal_height
            - self._interaction_height()
            - self._status_height()
            - self._queued_height()
            - self._content_input_gap_height(),
        )
        return min(self.menu.height(), available)

    def _process_viewer_height(self) -> int:
        """计算进程查看器占用的高度。"""
        if not self.bottom_pane.is_active("process_viewer"):
            return 0
        available = max(
            1,
            self.terminal_height
            - self._status_height()
            - self._queued_height()
            - self._content_input_gap_height(),
        )
        return min(self.process_viewer.height(), available)

    def _interaction_height(self) -> int:
        """返回输入区或审批区当前占用的高度。"""
        if self.bottom_pane.is_active("approval"):
            return self._approval_height()
        if self.bottom_pane.transient_active:
            return 0
        return self._input_stack_height()

    def _visible_height(self) -> int:
        """返回 inline 画布当前需要占用的终端行数。"""
        height = (
            self._transcript_dimension().preferred
            + self._status_height()
            + self._process_status_height()
            + self._queued_height()
            + self._menu_height()
            + self._process_viewer_height()
            + int(self._transcript_status_gap_visible())
            + self._content_input_gap_height()
            + self._interaction_height()
        )
        return max(1, min(self.terminal_height, height))

    def _content_input_gap_height(self) -> int:
        """返回正文状态区与底部交互区域之间的间距高度。"""
        if not self._content_input_gap_visible():
            return 0
        if (
            self.bottom_pane.is_active("menu")
            or self.bottom_pane.is_active("process_viewer")
        ):
            return self.COMMAND_SURFACE_GAP_HEIGHT
        if self.bottom_pane.transient_active:
            return self.OVERLAY_INPUT_GAP_HEIGHT
        return self.CONTENT_INPUT_GAP_HEIGHT

    def _content_input_gap_dimension(self) -> Dimension:
        """返回正文状态区与底部交互区域之间的间距尺寸。"""
        return Dimension.exact(self._content_input_gap_height())

    def _content_input_gap_visible(self) -> bool:
        """判断正文状态区与底部交互区域之间是否保留空行。"""
        return bool(
            self.document.has_visible_content
            or self.activity_block is not None
            or self.process_status.active
            or self._queued_content_visible()
        )

    def _transcript_status_gap_visible(self) -> bool:
        """判断正文与活动状态之间是否保留空行。"""
        return bool(
            self.document.has_visible_content
            and (self._status_height() or self._process_status_height())
            and not self.bottom_pane.is_active("menu")
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


def _erase_terminal_scrollback(output: Output) -> None:
    """清除支持 VT 擦除指令的终端滚屏缓冲区。"""
    if isinstance(output, (DummyOutput, PlainTextOutput)):
        return None
    if sys.platform == "win32" and not hasattr(output, "vt100_output"):
        return None
    output.write_raw("\x1b[3J")
    output.flush()


if __name__ == '__main__':
    pass
