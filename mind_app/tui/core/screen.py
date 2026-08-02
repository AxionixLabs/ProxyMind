# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import shutil
import typing
import asyncio
import contextlib
from dataclasses import dataclass
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.data_structures import (
    Point,
    Size
)
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
from prompt_toolkit.keys import Keys
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
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea
from mind_app.interaction.contracts import PromptContext
from mind_app.presentation.terminal_text import sanitize_terminal_text
from mind_core.design.terminal_capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities,
    TerminalKind
)
from mind_nova import const
from ..prompting.commands import completion_changes_input
from .approval import TuiApproval
from .approval_render import TUI_APPROVAL_STYLE
from .bottom_pane import (
    BottomSurface,
    TuiBottomPane
)
from .document import (
    TranscriptBlock,
    TranscriptLiveTail,
    TranscriptSnapshot,
    TuiDocument
)
from .input import (
    INPUT_BUFFER_NAME,
    TuiInputModel
)
from .interrupt import TuiInterruptState
from .keymap import (
    TuiKeyBinding,
    TuiRuntimeKeymap,
    binding_labels,
    primary_binding_label,
)
from .menu import (
    TUI_MENU_STYLE,
    TuiMenu
)
from .models import (
    FormattedText,
    FragmentBlock,
    TranscriptBacktrackRequest,
    TranscriptExportFormat,
    TranscriptExportResult
)
from .process_status import TuiProcessStatus
from .process_viewer import TuiProcessViewer
from .queued import (
    TuiPendingSteers,
    TuiQueuedMessages
)
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
from .transcript_overlay import TuiTranscriptOverlay


def _queued_message_edit_binding(capabilities: TerminalCapabilities) -> str:
    """返回当前终端适合展示的队尾编辑按键。"""
    identity = capabilities.identity
    if (
        identity.multiplexer == TerminalKind.TMUX
        or identity.kind in {
            TerminalKind.APPLE_TERMINAL,
            TerminalKind.VSCODE,
            TerminalKind.WARP,
        }
    ):
        return "shift + ←"
    return "alt + ↑"


@dataclass(frozen=True, slots=True)
class _InlineRendererState(object):
    """保存进入完整终端画面前的 renderer diff 状态。"""
    cursor_pos: Point
    last_screen: Screen | None
    last_size: Size | None
    last_style: str | None
    last_cursor_shape: CursorShape | None
    min_available_height: int


@dataclass(frozen=True, slots=True)
class FrameGeometry(object):
    """描述单次终端渲染使用的固定尺寸。"""
    width: int
    height: int
    revision: int


class TuiScreen(object):
    """持有单一 Application、视觉组件和布局尺寸策略。"""
    INPUT_MAX_LINES: typing.Final[int]               = 8
    QUEUED_MAX_HEIGHT: typing.Final[int]             = 6
    COMPLETION_MAX_HEIGHT: typing.Final[int]         = 8
    COMPLETION_COLUMN_MIN_WIDTH: typing.Final[int]   = 7
    CONTENT_INPUT_GAP_HEIGHT: typing.Final[int]      = 1
    OVERLAY_INPUT_GAP_HEIGHT: typing.Final[int]      = 1
    COMMAND_SURFACE_GAP_HEIGHT: typing.Final[int]    = 2
    INPUT_SURFACE_PADDING_HEIGHT: typing.Final[int]  = 1
    ESCAPE_SEQUENCE_TIMEOUT_SEC: typing.Final[float] = 0.1

    def __init__(
        self,
        *,
        input_model: TuiInputModel,
        document: TuiDocument,
        pending_steers: TuiPendingSteers,
        queued_messages: TuiQueuedMessages,
        interrupt_state: TuiInterruptState,
        get_context: typing.Callable[[], PromptContext],
        get_placeholder_text: typing.Callable[[], str],
        get_submission_deferred: typing.Callable[[], bool],
        get_queued_submission_text: typing.Callable[[], str | None],
        get_surface_submission_pending: typing.Callable[[], bool],
        can_transcript_backtrack: typing.Callable[[], bool],
        get_transcript_view_row: typing.Callable[[], int | None],
        accept_input: typing.Callable[[Buffer], bool],
        on_input_text_changed: typing.Callable[[Buffer], None],
        clear_exit_confirmation: typing.Callable[[], None],
        clear_visible_transcript: typing.Callable[[], None],
        scroll_transcript_page: typing.Callable[[int], None],
        toggle_transcript_overlay: typing.Callable[[], None],
        request_transcript_backtrack: typing.Callable[
            [TranscriptBacktrackRequest],
            None,
        ],
        report_missing_transcript_backtrack: typing.Callable[[], None],
        export_transcript: typing.Callable[
            [tuple[TranscriptBlock, ...], TranscriptExportFormat],
            TranscriptExportResult,
        ] | None,
        observe_terminal_width: typing.Callable[[int], None],
        keymap: TuiRuntimeKeymap,
        input_obj: Input | None = None,
        output_obj: Output | None = None,
        terminal_capabilities: TerminalCapabilities = (
            DEGRADED_TERMINAL_CAPABILITIES
        )
    ) -> None:
        self.input_model     = input_model
        self.document        = document
        self.pending_steers  = pending_steers
        self.queued_messages = queued_messages
        self.interrupt_state = interrupt_state

        self._queued_message_edit_binding = _queued_message_edit_binding(
            terminal_capabilities
        )

        self._get_context                    = get_context
        self._get_placeholder_text           = get_placeholder_text
        self._get_submission_deferred        = get_submission_deferred
        self._get_queued_submission_text     = get_queued_submission_text
        self._get_surface_submission_pending = get_surface_submission_pending
        self._get_transcript_view_row        = get_transcript_view_row

        self._can_transcript_backtrack = can_transcript_backtrack

        self._clear_exit_confirmation  = clear_exit_confirmation
        self._clear_visible_transcript = clear_visible_transcript

        self._scroll_transcript_page       = scroll_transcript_page
        self._toggle_transcript_overlay    = toggle_transcript_overlay
        self._request_transcript_backtrack = request_transcript_backtrack

        self._report_missing_transcript_backtrack = (
            report_missing_transcript_backtrack
        )

        self._export_transcript      = export_transcript
        self._observe_terminal_width = observe_terminal_width

        self.keymap = keymap

        self._validate_keymap(keymap)

        self._transcript_only: bool = False

        self._animation_tick: int = 0

        self._frame_geometry: FrameGeometry | None = None

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
        self.input.buffer.on_text_changed += (
            self.input_model.reopen_completion_menu
        )
        self.input.buffer.on_completions_changed += (
            self.input_model.select_default_completion
        )

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
            focus_input=self._focus_input,
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
        self.approval_footer_control = FormattedTextControl(
            self.approval.footer_fragments,
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

        self.transcript_overlay = TuiTranscriptOverlay(
            document=self.document,
            get_width=lambda: self.terminal_width,
            get_height=lambda: self._transcript_overlay_height(),
            get_snapshot=self._transcript_snapshot,
            invalidate=self.invalidate,
        )
        self.transcript_overlay_control = FormattedTextControl(
            self.transcript_overlay.visible_fragments,
            focusable=True,
            modal=True,
            key_bindings=self._transcript_overlay_key_bindings(),
        )

        self.transcript_window = Window(
            content=self.transcript_control,
            height=self._transcript_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
            get_line_prefix=self._transcript_line_prefix,
        )
        self.transcript_overlay_window = Window(
            content=self.transcript_overlay_control,
            height=self._transcript_overlay_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
            char=" ",
        )
        self.transcript_overlay_header = Window(
            content=FormattedTextControl(
                self._transcript_overlay_header_fragments
            ),
            height=self._transcript_overlay_header_dimension,
            dont_extend_height=True,
            char=" ",
        )
        self.transcript_overlay_footer = HSplit(
            [
                Window(
                    content=FormattedTextControl(
                        self._transcript_overlay_separator_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    content=FormattedTextControl(
                        self._transcript_overlay_primary_help_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    content=FormattedTextControl(
                        self._transcript_overlay_secondary_help_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
            ],
            height=self._transcript_overlay_footer_dimension,
            window_too_small=Window(),
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
        self.approval_footer_window = Window(
            content=self.approval_footer_control,
            height=self._approval_footer_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
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
        self.input_top_padding = Window(
            height=Dimension.exact(self.INPUT_SURFACE_PADDING_HEIGHT),
            char=" ",
            style="class:input-surface",
            dont_extend_height=True,
        )
        self.input_bottom_padding = Window(
            height=Dimension.exact(self.INPUT_SURFACE_PADDING_HEIGHT),
            char=" ",
            style="class:input-surface",
            dont_extend_height=True,
        )

        self.approval_card = ConditionalContainer(
            HSplit(
                [
                    self.approval_window,
                    self.approval_footer_window,
                ],
                align=VerticalAlign.TOP,
                window_too_small=Window(),
            ),
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
        self.completion_menu = CompletionsMenu(
            max_height=self.COMPLETION_MAX_HEIGHT,
            scroll_offset=1,
            extra_filter=Condition(self._native_completion_visible),
        )

        self.completion_menu_row = ConditionalContainer(
            VSplit([
                Window(
                    width=Dimension.exact(1),
                    char=" ",
                    dont_extend_width=True,
                ),
                self.completion_menu,
            ]),
            filter=Condition(self._native_completion_visible),
        )

        self.completion_fallback_control = FormattedTextControl(
            self._completion_fallback_fragments,
        )

        self.completion_fallback_window = Window(
            content=self.completion_fallback_control,
            height=Dimension.exact(1),
            dont_extend_width=True,
            style="class:completion-menu",
        )

        self.completion_fallback_row = ConditionalContainer(
            VSplit([
                Window(
                    width=Dimension.exact(1),
                    char=" ",
                    dont_extend_width=True,
                ),
                self.completion_fallback_window,
            ]),
            filter=Condition(self._completion_fallback_visible),
        )

        self.input_surface = HSplit(
            [
                self.input_top_padding,
                self.input,
                self.input_bottom_padding,
            ],
            align=VerticalAlign.TOP,
            height=self._input_surface_dimension,
            window_too_small=Window(),
        )
        self.input_footer = ConditionalContainer(
            self.footer_window,
            filter=Condition(self._footer_visible),
        )

        self.input_stack = HSplit(
            [
                self.input_surface,
                self.completion_menu_row,
                self.completion_fallback_row,
                self.input_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._input_stack_dimension,
            window_too_small=Window(),
        )
        self.input_area = ConditionalContainer(
            self.input_stack,
            filter=Condition(
                lambda: (
                    self.bottom_pane.input_visible
                    and not self._transcript_only
                )
            ),
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
        self.transcript_overlay_canvas = HSplit(
            [
                self.transcript_overlay_header,
                self.transcript_overlay_window,
                self.transcript_overlay_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._transcript_overlay_canvas_dimension,
            window_too_small=Window(),
        )
        self.root = HSplit(
            [
                ConditionalContainer(
                    self.canvas,
                    filter=Condition(lambda: not self.transcript_overlay.active),
                ),
                ConditionalContainer(
                    self.transcript_overlay_canvas,
                    filter=Condition(lambda: self.transcript_overlay.active),
                ),
            ],
            align=VerticalAlign.TOP,
            height=self._root_dimension,
            window_too_small=Window(),
        )

        dummy_io = (
            input_obj is None
            and output_obj is None
            and not (sys.stdin.isatty() and sys.stdout.isatty())
        )

        application_input  = input_obj or (DummyInput() if dummy_io else None)
        application_output = output_obj or (DummyOutput() if dummy_io else None)

        self.application: Application[None] = Application(
            layout=Layout(self.root, focused_element=self.input),
            key_bindings=merge_key_bindings([
                self.input_model.key_bindings,
                self._transcript_key_bindings(),
            ]),
            style=build_tui_application_style(
                self.input_model.style,
                TUI_APPROVAL_STYLE,
                TUI_MENU_STYLE,
                capabilities=terminal_capabilities,
            ),
            full_screen=False,
            erase_when_done=False,
            mouse_support=False,
            max_render_postpone_time=None,
            before_render=self._capture_frame_geometry,
            after_render=self._release_frame_geometry,
            input=application_input,
            output=application_output,
        )

        self.application.ttimeoutlen = self.ESCAPE_SEQUENCE_TIMEOUT_SEC

        self._inline_renderer_state: _InlineRendererState | None = None

    @property
    def terminal_width(self) -> int:
        """返回当前渲染输出的终端列数。"""
        return self.frame_geometry.width

    @property
    def terminal_height(self) -> int:
        """返回当前渲染输出的终端行数。"""
        return self.frame_geometry.height

    @property
    def frame_geometry(self) -> FrameGeometry:
        """返回最近一帧固定使用的终端尺寸。"""
        geometry = self._frame_geometry
        if geometry is not None:
            return geometry
        return self._read_frame_geometry(revision=0)

    def _capture_frame_geometry(self, application: Application[None]) -> None:
        """在布局计算前固定当前帧使用的终端尺寸。"""
        self._frame_geometry = self._read_frame_geometry(
            revision=application.render_counter,
        )
        self._observe_terminal_width(self._frame_geometry.width)

    def _release_frame_geometry(self, application: Application[None]) -> None:
        """在渲染结束后恢复终端尺寸的实时读取。"""
        _ = application
        self._frame_geometry = None

    def _read_frame_geometry(self, *, revision: int) -> FrameGeometry:
        """读取并规范化一个终端尺寸快照。"""
        width, height = self._output_size()
        return FrameGeometry(
            width=max(20, width),
            height=max(1, height),
            revision=max(0, int(revision)),
        )

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

    def set_keymap(self, keymap: TuiRuntimeKeymap) -> None:
        """在 Application 启动前替换主视图和记录面板按键。"""
        if self.application.is_running:
            raise RuntimeError("cannot configure TUI keymap while running")
        self._validate_keymap(keymap)
        self.keymap = keymap
        self.transcript_overlay_control.key_bindings = (
            self._transcript_overlay_key_bindings()
        )
        self.application.key_bindings = merge_key_bindings([
            self.input_model.key_bindings,
            self._transcript_key_bindings(),
        ])
        self.invalidate()

    def _validate_keymap(self, keymap: TuiRuntimeKeymap) -> None:
        """校验全局记录入口不会覆盖现有主输入动作。"""
        reserved = [
            (f"tui.input.binding[{index}]", tuple(binding.keys))
            for index, binding in enumerate(self.input_model.key_bindings.bindings)
        ]
        fixed = KeyBindings()
        for action, key in (
            ("tui.transcript.clear", "c-l"),
            ("tui.transcript.page_up", "pageup"),
            ("tui.transcript.page_down", "pagedown"),
        ):
            fixed.add(key)(lambda event: None)
            reserved.append((action, tuple(fixed.bindings[-1].keys)))
        keymap.validate_main_conflicts(reserved)

    def set_transcript_only(self, active: bool) -> None:
        """切换为只保留正文的终端画布。"""
        self._transcript_only = bool(active)
        self.invalidate()

    def set_transcript_overlay(self, active: bool) -> bool:
        """切换完整会话记录、终端画面和键盘焦点。"""
        active = bool(active)
        if active == self.transcript_overlay.active:
            return False
        if active and self._transcript_overlay_blocked():
            return False

        if active:
            try:
                self._enter_transcript_screen()
                self.transcript_overlay.open()
                self.application.layout.focus(self.transcript_overlay_control)
            except BaseException:
                self.transcript_overlay.active = False
                try:
                    self._leave_transcript_screen()
                finally:
                    self._restore_transcript_focus()
                raise
        else:
            try:
                self.transcript_overlay.close()
            finally:
                try:
                    self._leave_transcript_screen()
                finally:
                    self._restore_transcript_focus()
        self.invalidate()
        return True

    def _restore_transcript_focus(self) -> None:
        """把完整记录关闭后的焦点恢复到当前交互表面。"""
        surface = self.bottom_pane.active_surface
        if surface is None:
            self._focus_input()
        else:
            self._focus_bottom_surface(surface)

    def _enter_transcript_screen(self) -> None:
        """保存 inline 渲染状态并准备完整终端画面。"""
        renderer = self.application.renderer
        if self._inline_renderer_state is not None:
            return None

        # prompt_toolkit 没有运行中切换全屏的公开接口；固定版本下保留
        # inline diff 状态，退出 alternate screen 后才能原位继续渲染。
        self._inline_renderer_state = _InlineRendererState(
            cursor_pos=renderer._cursor_pos,
            last_screen=renderer._last_screen,
            last_size=renderer._last_size,
            last_style=renderer._last_style,
            last_cursor_shape=renderer._last_cursor_shape,
            min_available_height=renderer._min_available_height,
        )

        self.application.full_screen = True

        renderer.full_screen           = True
        renderer._cursor_pos           = Point(x=0, y=0)
        renderer._last_screen          = None
        renderer._last_size            = None
        renderer._last_style           = None
        renderer._last_cursor_shape    = None
        renderer._min_available_height = self.terminal_height

    def _leave_transcript_screen(self) -> None:
        """退出完整终端画面并恢复 inline 渲染状态。"""
        renderer = self.application.renderer
        state = self._inline_renderer_state

        try:
            if renderer._in_alternate_screen:
                renderer.output.quit_alternate_screen()
                renderer.output.flush()
        finally:
            renderer._in_alternate_screen = False
            self.application.full_screen  = False
            renderer.full_screen          = False

            if state is not None:
                renderer._cursor_pos           = state.cursor_pos
                renderer._last_screen          = state.last_screen
                renderer._last_size            = state.last_size
                renderer._last_style           = state.last_style
                renderer._last_cursor_shape    = state.last_cursor_shape
                renderer._min_available_height = state.min_available_height

            self._inline_renderer_state = None

    def set_activity_renderable(self, block: FragmentBlock) -> None:
        """替换活动状态区域的展示内容。"""
        self.activity_block = block
        self._animation_tick += 1
        self.transcript_overlay.content_changed()
        self.invalidate()

    def clear_activity_renderable(self) -> None:
        """清空活动状态区域的展示内容。"""
        changed = self.activity_block is not None
        self.activity_block = None
        if changed:
            self._animation_tick += 1
            self.transcript_overlay.content_changed()
        self.invalidate()

    def clear_terminal_scrollback(self) -> None:
        """清除当前画布及终端滚屏缓冲区。"""
        self.application.renderer.clear()
        _erase_terminal_scrollback(self.application.output)
        self.invalidate()

    def print_exit_summary(self, session_id: str) -> None:
        """在 Application 停止后向终端打印会话恢复提示。"""
        with contextlib.suppress(EOFError, OSError, ValueError):
            print_formatted_text(
                PromptFormattedText(
                    (
                        ("", "\n"),
                        *exit_summary_fragments(session_id),
                    ),
                ),
                output=self.application.output,
                include_default_pygments_style=False,
            )

    def transcript_fragments(self) -> FormattedText:
        """生成会话内容区域的格式化片段。"""
        return self.document.fragments(width=self.terminal_width)

    def _transcript_snapshot(self) -> TranscriptSnapshot:
        """组合已提交记录和当前画面专用的动态尾部。"""
        snapshot = self.document.transcript_snapshot()
        activity = self.activity_block
        if activity is None:
            return snapshot

        document_tail = snapshot.live_tail

        cells = list(document_tail.cells) if document_tail is not None else []
        cells.append(TranscriptBlock(
            display_block=activity,
            transcript_block=activity,
            kind="operation",
            gap_before=bool(snapshot.committed_cells or cells),
            stream_continuation=False,
            transcript_stable=False,
        ))

        return TranscriptSnapshot(
            committed_cells=snapshot.committed_cells,
            live_tail=TranscriptLiveTail(
                cells=tuple(cells),
                revision=(
                    document_tail.revision
                    if document_tail is not None
                    else self.document.active_transcript_revision
                ),
                stream_continuation=(
                    document_tail.stream_continuation
                    if document_tail is not None
                    else False
                ),
                animation_tick=self._animation_tick,
            ),
            committed_revision=snapshot.committed_revision,
        )

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

        if (
            hasattr(self, "transcript_overlay")
            and self.transcript_overlay.active
        ):
            self.application.layout.focus(self.transcript_overlay_control)
            return None

        controls = {
            "approval": self.approval_control,
            "menu": self.menu_control,
            "process_viewer": self.process_viewer_control,
        }
        self.application.layout.focus(controls[surface])

    def _focus_input(self) -> None:
        """把焦点路由到当前顶层记录或主输入控件。"""
        if (
            hasattr(self, "transcript_overlay")
            and self.transcript_overlay.active
        ):
            self.application.layout.focus(self.transcript_overlay_control)
            return None

        self.application.layout.focus(self.input)

    def _input_line_prefix(
        self,
        line_number: int,
        wrap_count: int
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
        if (
            wrap_count <= 0
            or not self._assistant_line(self.transcript_fragments(), line_number)
        ):
            return []

        return [(ASSISTANT_PREFIX_CLASS, "  ")]

    def _transcript_fragments(self) -> FormattedText:
        """返回正文控件使用的格式化片段。"""
        return self.transcript_fragments()

    def _placeholder_fragments(self) -> StyleAndTextTuples:
        """返回当前输入轮次固定的占位文案。"""
        return [("class:placeholder", f" {self._get_placeholder_text()}")]

    @staticmethod
    def _assistant_line(
        fragments: FormattedText,
        target_line: int
    ) -> bool:
        """判断指定正文逻辑行是否属于助手正文块。"""
        line_number   = 0
        at_line_start = True

        for style, text in fragments:
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
        pending_active = self.pending_steers.active
        queued_active  = self.queued_messages.active

        if pending_active and queued_active:
            pending_rows = 3
            queued_rows  = self.QUEUED_MAX_HEIGHT - pending_rows
        else:
            pending_rows = self.QUEUED_MAX_HEIGHT
            queued_rows  = self.QUEUED_MAX_HEIGHT

        pending = self.pending_steers.fragments(
            width=self.terminal_width,
            max_rows=pending_rows,
        )
        queued = self.queued_messages.fragments(
            width=self.terminal_width,
            max_rows=queued_rows,
            edit_binding=self._queued_message_edit_binding,
        )
        if pending and queued:
            return [*pending, ("", "\n"), *queued]
        return pending or queued

    def _footer_fragments(self) -> FormattedText:
        """生成单行 TUI 信息栏。"""
        if self.input_model.history_backtrack_primed:
            return [
                ("class:footer.exit-key", "Esc"),
                ("class:footer.exit-hint", " again to edit previous message"),
            ]
        if self.interrupt_state.exit_armed:
            return [
                ("class:footer.exit-key", "Ctrl + C"),
                ("class:footer.exit-hint", " again to exit"),
            ]
        if self._queue_submission_hint_visible():
            full_hint = "  tab to queue message"
            hint = (
                full_hint
                if get_cwidth(full_hint) <= self.terminal_width
                else "  tab to queue"
            )
            return [("class:footer.queue-hint", hint)]
        if (
            self.document.has_pending_submission
            or self._get_surface_submission_pending()
        ):
            return []

        context = self._get_context()
        theme   = self.input_model.theme(context.mode)

        parts: FormattedText = [(f"fg:{theme['brand']}", const.APP_DESC)]

        permissions_label = sanitize_terminal_text(context.permissions_label).strip()

        access_style = (
            "class:footer.access.full"
            if permissions_label.lower() == "full access"
            else "class:footer.access"
        )
        values = [
            ("class:footer.model", context.model or "-"),
            (access_style, permissions_label),
            ("class:footer.workspace", context.workspace_label),
        ]
        for style, value in values:
            text = sanitize_terminal_text(value).strip()
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

        overlay_available = Condition(
            lambda: (
                not self.transcript_overlay.active
                and not self._transcript_overlay_blocked()
            )
        )

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

        def open_transcript(event) -> None:
            _ = event
            self._toggle_transcript_overlay()

        self._add_configured_bindings(
            bindings,
            self.keymap.open_transcript,
            open_transcript,
            binding_filter=overlay_available,
        )

        return bindings

    def _transcript_overlay_key_bindings(self) -> KeyBindings:
        """创建完整会话记录的模态按键。"""
        bindings = KeyBindings()
        pager    = self.keymap.pager

        search_editing = Condition(
            lambda: self.transcript_overlay.search_editing
        )

        browsing = ~search_editing

        @bindings.add("escape", eager=True, filter=browsing)
        def _(event) -> None:
            _ = event
            if not self._can_transcript_backtrack():
                self._toggle_transcript_overlay()
                return None
            if not self.transcript_overlay.begin_or_step_backtrack():
                self._toggle_transcript_overlay()
                self._report_missing_transcript_backtrack()

        @bindings.add("left", eager=True, filter=browsing)
        def _(event) -> None:
            _ = event
            if self.transcript_overlay.backtrack_active:
                self.transcript_overlay.begin_or_step_backtrack()

        @bindings.add("right", eager=True, filter=browsing)
        def _(event) -> None:
            _ = event
            self.transcript_overlay.step_backtrack_forward()

        @bindings.add("enter", eager=True, filter=browsing)
        def _(event) -> None:
            _ = event
            request = self.transcript_overlay.confirm_backtrack()
            if request is None:
                return None
            self._toggle_transcript_overlay()
            self._request_transcript_backtrack(request)

        def toggle_raw(event) -> None:
            _ = event
            self.transcript_overlay.toggle_raw_mode()

        self._add_configured_bindings(
            bindings,
            pager.toggle_raw,
            toggle_raw,
            binding_filter=browsing,
        )

        def begin_search(event) -> None:
            _ = event
            self.transcript_overlay.begin_search()
        self._add_configured_bindings(
            bindings,
            pager.search,
            begin_search,
            binding_filter=browsing,
        )

        def search_next(event) -> None:
            _ = event
            self.transcript_overlay.step_search(1)
        self._add_configured_bindings(
            bindings,
            pager.search_next,
            search_next,
            binding_filter=browsing,
        )

        def search_previous(event) -> None:
            _ = event
            self.transcript_overlay.step_search(-1)
        self._add_configured_bindings(
            bindings,
            pager.search_previous,
            search_previous,
            binding_filter=browsing,
        )

        def export_transcript(event) -> None:
            handler = self._export_transcript
            if handler is None:
                self.transcript_overlay.set_export_status(
                    "Export unavailable.",
                    failed=True,
                )
                return None

            output_format: TranscriptExportFormat = (
                "raw" if self.transcript_overlay.raw_mode else "markdown"
            )
            if not self.transcript_overlay.begin_export(output_format):
                return None

            cells = self.document.transcript_snapshot().committed_cells

            async def run_export() -> None:
                """在线程中写入记录文件并把结果返回当前覆盖层。"""
                try:
                    result = await asyncio.to_thread(
                        handler,
                        cells,
                        output_format,
                    )
                except Exception as error:
                    detail = sanitize_terminal_text(str(error)).strip()
                    self.transcript_overlay.set_export_status(
                        f"Export failed: {detail or type(error).__name__}",
                        failed=True,
                    )
                    return None

                self.transcript_overlay.set_export_status(
                    f"Exported {result.format}: {result.path}",
                    failed=False,
                )

            event.app.create_background_task(
                run_export(),
            )

        self._add_configured_bindings(
            bindings,
            pager.export,
            export_transcript,
            binding_filter=browsing,
        )

        def close(event) -> None:
            _ = event
            self._toggle_transcript_overlay()
        self._add_configured_bindings(
            bindings,
            (*pager.close, *pager.close_transcript),
            close,
            binding_filter=browsing,
        )

        def scroll_up(event) -> None:
            _ = event
            self.transcript_overlay.scroll_line(-1)
        self._add_configured_bindings(
            bindings,
            pager.scroll_up,
            scroll_up,
            binding_filter=browsing,
        )

        def scroll_down(event) -> None:
            _ = event
            self.transcript_overlay.scroll_line(1)
        self._add_configured_bindings(
            bindings,
            pager.scroll_down,
            scroll_down,
            binding_filter=browsing,
        )

        def page_up(event) -> None:
            _ = event
            self.transcript_overlay.scroll_page(-1)
        self._add_configured_bindings(
            bindings,
            pager.page_up,
            page_up,
            binding_filter=browsing,
        )

        def page_down(event) -> None:
            _ = event
            self.transcript_overlay.scroll_page(1)
        self._add_configured_bindings(
            bindings,
            pager.page_down,
            page_down,
            binding_filter=browsing,
        )

        def half_page_up(event) -> None:
            _ = event
            self.transcript_overlay.scroll_half_page(-1)
        self._add_configured_bindings(
            bindings,
            pager.half_page_up,
            half_page_up,
            binding_filter=browsing,
        )

        def half_page_down(event) -> None:
            _ = event
            self.transcript_overlay.scroll_half_page(1)
        self._add_configured_bindings(
            bindings,
            pager.half_page_down,
            half_page_down,
            binding_filter=browsing,
        )

        def jump_top(event) -> None:
            _ = event
            self.transcript_overlay.jump_top()
        self._add_configured_bindings(
            bindings,
            pager.jump_top,
            jump_top,
            binding_filter=browsing,
        )

        def jump_bottom(event) -> None:
            _ = event
            self.transcript_overlay.jump_bottom()
        self._add_configured_bindings(
            bindings,
            pager.jump_bottom,
            jump_bottom,
            binding_filter=browsing,
        )

        @bindings.add("escape", eager=True, filter=search_editing)
        def _(event) -> None:
            _ = event
            self.transcript_overlay.cancel_search()

        @bindings.add("enter", eager=True, filter=search_editing)
        def _(event) -> None:
            _ = event
            self.transcript_overlay.confirm_search()

        @bindings.add("backspace", eager=True, filter=search_editing)
        @bindings.add("c-h", eager=True, filter=search_editing)
        def _(event) -> None:
            _ = event
            self.transcript_overlay.backspace_search()

        @bindings.add(Keys.Any, eager=True, filter=search_editing)
        def _(event) -> None:
            self.transcript_overlay.append_search_text(event.data)

        return bindings

    @staticmethod
    def _add_configured_bindings(
        bindings: KeyBindings,
        configured: tuple[TuiKeyBinding, ...],
        handler: typing.Callable[[typing.Any], None],
        *,
        binding_filter: typing.Any = True,
    ) -> None:
        """把已解析的按键序列注册到一个输入上下文。"""
        for binding in configured:
            bindings.add(
                *binding.keys,
                eager=True,
                filter=binding_filter,
            )(handler)

    def _canvas_dimension(self) -> Dimension:
        """返回随内容自然增长并受终端高度限制的画布高度。"""
        return Dimension.exact(self._visible_height())

    def _root_dimension(self) -> Dimension:
        """返回当前主画布或完整记录画布所需高度。"""
        if self.transcript_overlay.active:
            return self._transcript_overlay_canvas_dimension()
        return self._canvas_dimension()

    def _transcript_overlay_height(self) -> int:
        """返回完整记录正文区域可用高度。"""
        return max(
            0,
            self.terminal_height
            - self._transcript_overlay_header_height()
            - self._transcript_overlay_footer_height(),
        )

    def _transcript_overlay_header_height(self) -> int:
        """返回完整记录标题区域高度。"""
        return min(1, self.terminal_height)

    def _transcript_overlay_footer_height(self) -> int:
        """返回完整记录底栏高度。"""
        return min(4, max(0, self.terminal_height - 1))

    def _transcript_overlay_header_dimension(self) -> Dimension:
        """返回完整记录标题区域尺寸。"""
        return Dimension.exact(self._transcript_overlay_header_height())

    def _transcript_overlay_footer_dimension(self) -> Dimension:
        """返回完整记录底栏尺寸。"""
        return Dimension.exact(self._transcript_overlay_footer_height())

    def _transcript_overlay_dimension(self) -> Dimension:
        """返回完整记录正文区域尺寸。"""
        return Dimension.exact(self._transcript_overlay_height())

    def _transcript_overlay_canvas_dimension(self) -> Dimension:
        """返回完整记录画布尺寸。"""
        return Dimension.exact(self.terminal_height)

    def _transcript_overlay_header_fragments(self) -> FormattedText:
        """生成标题覆盖在装饰图案上的单行页眉。"""
        width   = self.terminal_width
        pattern = ("/ " * ((width + 1) // 2))[:width]

        title = (
            "/ R A W   T R A N S C R I P T"
            if self.transcript_overlay.raw_mode
            else "/ T R A N S C R I P T"
        )

        if len(title) >= width:
            return [("class:transcript.overlay.title", title[:width])]

        return [
            ("class:transcript.overlay.title", title),
            ("class:transcript.overlay.rule", pattern[len(title):]),
        ]

    def _transcript_overlay_separator_fragments(self) -> FormattedText:
        """生成包含滚动百分比的底栏分隔线。"""
        width = self.terminal_width
        percentage = self.transcript_overlay.scroll_percentage()
        progress = f" {percentage}% "
        progress_start = max(0, width - len(progress) - 1)
        return [
            ("class:transcript.overlay.rule", "─" * progress_start),
            ("class:transcript.overlay.progress", progress),
            (
                "class:transcript.overlay.rule",
                "─" * max(0, width - progress_start - len(progress)),
            ),
        ]

    def _transcript_overlay_primary_help_fragments(self) -> FormattedText:
        """生成完整记录的滚动提示。"""
        if self.transcript_overlay.search_editing:
            return [
                ("class:transcript.overlay.search-prompt", "/ "),
                (
                    "class:transcript.overlay.search-query",
                    self.transcript_overlay.search_query,
                ),
                ("class:transcript.overlay.search-cursor", "█"),
            ]
        if self.transcript_overlay.backtrack_active:
            return [(
                "class:transcript.overlay.help",
                " Esc/Left previous   Right next   Enter edit",
            )]
        if self.transcript_overlay.export_status:
            style = (
                "class:transcript.overlay.export-error"
                if self.transcript_overlay.export_failed
                else "class:transcript.overlay.export-success"
            )
            return [(style, f" {self.transcript_overlay.export_status}")]

        pager         = self.keymap.pager
        raw_label     = primary_binding_label(pager.toggle_raw)
        search_label  = primary_binding_label(pager.search)
        export_label  = primary_binding_label(pager.export)
        search_status = ""

        if self.transcript_overlay.search_query:
            current, total = self.transcript_overlay.search_result_position
            search_status = (
                f"{current}/{total} {self.transcript_overlay.search_query}"
            )

        scroll_hint = self._paired_key_hint(
            pager.scroll_up,
            pager.scroll_down,
            "to scroll",
        )
        raw_hint = (
            f"{raw_label} "
            f"{'rich' if self.transcript_overlay.raw_mode else 'raw'}"
            if raw_label
            else ""
        )
        hints = (
            (search_status, scroll_hint, raw_hint)
            if search_status
            else (
                scroll_hint,
                self._paired_key_hint(
                    pager.page_up,
                    pager.page_down,
                    "to page",
                ),
                self._paired_key_hint(
                    pager.jump_top,
                    pager.jump_bottom,
                    "to jump",
                ),
                raw_hint,
                f"{search_label} search" if search_label else "",
                f"{export_label} export" if export_label else "",
            )
        )

        return [("class:transcript.overlay.help", self._help_line(hints))]

    def _transcript_overlay_secondary_help_fragments(self) -> FormattedText:
        """生成完整记录的跳转和退出提示。"""
        pager = self.keymap.pager
        close = binding_labels((*pager.close, *pager.close_transcript))

        if self.transcript_overlay.search_editing:
            return [(
                "class:transcript.overlay.help",
                " Enter search   Esc cancel",
            )]

        if self.transcript_overlay.backtrack_active:
            hint = f" {close} to cancel" if close else ""
            return [("class:transcript.overlay.help", hint)]

        has_target = (
            self._can_transcript_backtrack()
            and self.transcript_overlay.has_backtrack_target
        )

        close_hint = (
            f"Esc/{close} to quit"
            if close and not has_target
            else f"{close} to quit" if close else "Esc to quit"
        )

        hints = (
            "Esc to edit previous" if has_target else "",
            close_hint,
            self._paired_key_hint(
                pager.search_next,
                pager.search_previous,
                "search result",
            ) if self.transcript_overlay.search_query else "",
            self._paired_key_hint(
                pager.half_page_up,
                pager.half_page_down,
                "half page",
            ),
        )

        return [("class:transcript.overlay.help", self._help_line(hints))]

    @staticmethod
    def _paired_key_hint(
        first: tuple[TuiKeyBinding, ...],
        second: tuple[TuiKeyBinding, ...],
        suffix: str
    ) -> str:
        """生成两个互补动作的首选按键提示。"""
        labels = "/".join(filter(None, (
            primary_binding_label(first),
            primary_binding_label(second),
        )))
        return f"{labels} {suffix}" if labels else ""

    @staticmethod
    def _help_line(hints: typing.Iterable[str]) -> str:
        """组合一行非空的完整记录操作提示。"""
        content = "   ".join(hint for hint in hints if hint)
        return f" {content}" if content else ""

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

    def _input_surface_dimension(self) -> Dimension:
        """返回包含上下留白的输入表面高度。"""
        return Dimension.exact(self._input_surface_height())

    def _input_stack_dimension(self) -> Dimension:
        """返回输入框、补全列表和当前可见 footer 的总高度。"""
        return Dimension.exact(self._input_stack_height())

    def _approval_dimension(self) -> Dimension:
        """返回审批卡背景区域的当前显示高度。"""
        return Dimension.exact(self._approval_card_height())

    def _approval_footer_dimension(self) -> Dimension:
        """返回审批卡透明提示区域的当前显示高度。"""
        return Dimension.exact(self._approval_footer_height())

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
        if self._transcript_only or self.bottom_pane.is_active("approval"):
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
        """返回当前输入区 footer 占用高度。"""
        return 1 if self._footer_visible() else 0

    def _footer_visible(self) -> bool:
        """判断输入框下方的信息栏是否应当显示。"""
        return bool(
            not self._transcript_only
            and not self._overlay_active()
            and self.terminal_height > self._input_surface_height()
        )

    def _queue_submission_hint_visible(self) -> bool:
        """判断执行期间是否应显示输入排队提示。"""
        return bool(
            self._get_submission_deferred()
            and self.input.buffer.text != self._get_queued_submission_text()
            and (self.input.buffer.text.strip() or self.input_model.shell_mode)
        )

    def _queued_content_visible(self) -> bool:
        """判断待提交区域是否存在消息。"""
        return bool(
            self.pending_steers.active
            or self.queued_messages.active
        )

    def _overlay_active(self) -> bool:
        """判断补全、选择菜单或审批层是否正在显示。"""
        return bool(self.bottom_pane.transient_active or self._completion_visible())

    def _transcript_overlay_blocked(self) -> bool:
        """判断当前临时表面是否禁止打开完整会话记录。"""
        return bool(
            self.bottom_pane.is_active("approval")
            or self.bottom_pane.is_active("menu")
            or self._completion_visible()
        )

    def _completion_visible(self) -> bool:
        """判断输入框是否存在可展示的补全候选项。"""
        state = self.input.buffer.complete_state
        return bool(
            self.bottom_pane.input_visible
            and (
                self._completion_fallback_visible()
                or (state is not None and state.completions)
                or self._expected_completion_count()
            )
        )

    def _native_completion_visible(self) -> bool:
        """判断原生补全候选列表是否应当显示。"""
        state = self.input.buffer.complete_state
        return bool(
            self.bottom_pane.input_visible
            and not self._completion_fallback_visible()
            and state is not None
            and state.completions
        )

    def _completion_fallback_visible(self) -> bool:
        """判断精确命令或空结果状态是否应当显示。"""
        return bool(
            self.bottom_pane.input_visible
            and self._completion_fallback_fragments()
        )

    def _completion_fallback_fragments(self) -> PromptFormattedText:
        """返回精确命令或空结果状态使用的展示片段。"""
        completions = self.input_model.completion_menu_completions(
            self.input.buffer.document
        )

        if completions is None:
            return PromptFormattedText()

        if not completions:
            return PromptFormattedText([
                ("class:completion-menu.empty", " no matches"),
            ])

        if (
            len(completions) != 1
            or completion_changes_input(
                self.input.buffer.document,
                completions[0],
            )
        ):
            return PromptFormattedText()

        completion    = completions[0]
        display_width = get_cwidth(completion.display_text)

        command_width = max(
            self.COMPLETION_COLUMN_MIN_WIDTH,
            display_width + 2,
        )

        command_padding = " " * (command_width - display_width - 1)

        fragments: StyleAndTextTuples = [
            (
                "class:completion-menu.completion.current",
                f" {completion.display_text}{command_padding}",
            ),
        ]

        if completion.display_meta_text:
            fragments.append((
                "class:completion-menu.meta.completion.current",
                f" {completion.display_meta_text} ",
            ))

        return PromptFormattedText(fragments)

    def _expected_completion_count(self) -> int:
        """同步计算当前输入应展示的补全项数量。"""
        if not self.bottom_pane.input_visible:
            return 0

        document = self.input.buffer.document

        raw_completions = self.input_model.completer.menu_completions(
            document
        )

        completions = self.input_model.completion_menu_completions(document)

        if raw_completions is not None:
            if completions is None:
                return 0
            return max(1, len(completions))

        return len(self.input_model.completer.matching_completions(
            document
        ))

    def _completion_height(self) -> int:
        """计算无边框补全列表占用行数。"""
        if not self._completion_visible():
            return 0

        state        = self.input.buffer.complete_state
        loaded_count = len(state.completions) if state is not None else 0
        count        = max(loaded_count, self._expected_completion_count())
        available    = max(1, self.terminal_height - self._input_surface_height())

        return min(self.COMPLETION_MAX_HEIGHT, count, available)

    def _completion_section_height(self) -> int:
        """返回补全列表占用高度。"""
        return self._completion_height()

    def _input_stack_height(self) -> int:
        """返回当前完整输入区域占用高度。"""
        return (
            self._input_surface_height()
            + self._completion_section_height()
            + self._footer_height()
        )

    def _input_surface_height(self) -> int:
        """计算输入内容与上下留白共同占用的高度。"""
        return self._input_height() + self.INPUT_SURFACE_PADDING_HEIGHT * 2

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

        return min(
            self._approval_card_height() + self._approval_footer_height(),
            self._approval_available_height(),
        )

    def _approval_card_height(self) -> int:
        """计算审批卡背景区域占用的显示行数。"""
        if not self.bottom_pane.is_active("approval"):
            return 0

        text = fragments_text(self.approval.fragments())
        if not text:
            return 0
        return display_line_count(text, width=self.terminal_width)

    def _approval_footer_height(self) -> int:
        """计算审批卡透明提示区域占用的显示行数。"""
        if not self.bottom_pane.is_active("approval"):
            return 0

        text = fragments_text(self.approval.footer_fragments())
        if not text:
            return 0
        return display_line_count(text, width=self.terminal_width)

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
        if self._transcript_only:
            return 0
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
            not self._transcript_only
            and (
                self.document.has_visible_content
                or self.activity_block is not None
                or self.process_status.active
                or self._queued_content_visible()
            )
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
