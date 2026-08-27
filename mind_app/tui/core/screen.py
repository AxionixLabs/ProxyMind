# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import shutil
import typing
import asyncio
import contextlib
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
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
    StyleAndTextTuples
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
    ScrollOffsets,
    VerticalAlign,
    VSplit,
    Window
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.processors import (
    AfterInput,
    ConditionalProcessor
)
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.widgets import TextArea
from mind_app.interaction.contracts import PromptContext
from mind_app.presentation.terminal_text import sanitize_terminal_text
from mind_core.design.terminal_capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities,
)
from ..prompting.commands import (
    completion_changes_input,
    slash_command_query
)
from ..prompting.skills import skill_query_token
from .approval import TuiApproval
from .approval_render import TUI_APPROVAL_STYLE
from .bottom_pane import (
    BottomSurface,
    TuiBottomPane
)
from .document import (
    TranscriptBlock,
    TuiDocument
)
from .directory_trust import TuiDirectoryTrust
from .input import (
    INPUT_BUFFER_NAME,
    TuiInputModel
)
from .hyperlinks import (
    TerminalHyperlinkOutput,
    TerminalHyperlinkWindow
)
from .interrupt import TuiInterruptState
from .keymap import (
    TuiKeyBinding,
    TuiRuntimeKeymap,
    binding_labels,
    primary_binding_label
)
from .mailbox import (
    TuiMailboxOverlay,
    format_mailbox_count,
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
from .resume_picker import TuiResumePicker
from .queued import (
    TuiPendingSteers,
    TuiQueuedMessages
)
from ..rendering.fragments import (
    clip_fragments,
    cursor_point,
    cursor_point_for_display_row,
    display_line_count,
    fragment_continuation_widths,
    fragments_text,
    join_formatted_lines,
    split_formatted_lines
)
from .styles import (
    ASSISTANT_PREFIX_CLASS,
    build_tui_application_style,
    exit_summary_fragments
)
from .token_menu import (
    TOKEN_MENU_LEFT_PADDING,
    TokenCompletionMenuControl,
    token_menu_display_height
)
from .transcript_overlay import TuiTranscriptOverlay
from .static_pager import TuiStaticPager
from ..rendering.screen.geometry import (
    ActiveViewLayout,
    BottomPaneLayout,
    ComposerLayout,
    FrameGeometry,
    InlineRendererState as _InlineRendererState,
    OverlayLayout
)
from ..rendering.screen.layout import (
    allocate_approval_view_layout,
    allocate_auxiliary_pane_layout,
    allocate_menu_view_layout,
    measure_composer_layout,
    measure_overlay_layout
)
from ..rendering.screen.overlays import (
    mailbox_header_fragments,
    mailbox_separator_fragments,
    static_pager_header_fragments,
    static_pager_separator_fragments,
    transcript_header_fragments,
    transcript_separator_fragments
)
from ..rendering.screen.surfaces import (
    FooterMode,
    completion_candidate_fragments,
    completion_empty_fragments,
    completion_hint_fragments,
    mention_completion_hint_fragments,
    footer_fragments as render_footer_fragments,
    input_prompt_fragments,
    join_queued_fragments,
    placeholder_fragments,
    queued_row_budget,
    resolve_footer_mode
)
from ..rendering.screen.terminal import (
    clear_terminal_for_resize_replay as _clear_terminal_for_resize_replay,
    erase_terminal_scrollback as _erase_terminal_scrollback,
    queued_message_edit_binding as _queued_message_edit_binding,
    set_alternate_scroll_mode as _set_alternate_scroll_mode,
    set_synchronized_output as _set_synchronized_output,
    supports_vt_control as _supports_vt_control
)
from ..contracts.resume import (
    ResumePickerRequest,
    ResumePickerResult,
    ResumePreview,
    ResumeRow
)
from ..contracts.transcript import MailboxEntry
from ..contracts.pager import StaticPagerRequest
from ..contracts.screen import (
    MailboxScreenPort,
    ResumePickerScreenPort
)


class TuiScreen(MailboxScreenPort, ResumePickerScreenPort):
    """持有单一 Application、视觉组件和布局尺寸策略。"""

    INPUT_TEXT_LEFT_MARGIN: typing.Final[int]        = 2
    QUEUED_MAX_HEIGHT: typing.Final[int]             = 6
    COMPLETION_MAX_HEIGHT: typing.Final[int]         = 8
    COMPLETION_HINT_HEIGHT: typing.Final[int]        = 2
    COMPLETION_COLUMN_MIN_WIDTH: typing.Final[int]   = 7
    CONTENT_SURFACE_GAP_HEIGHT: typing.Final[int]    = 1
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
        close_mailbox_overlay: typing.Callable[[], None],
        close_static_pager: typing.Callable[[], None],
        request_resume_preview: typing.Callable[[ResumeRow, int, int], None],
        request_resume_transcript: typing.Callable[[ResumeRow, int, int], None],
        cancel_resume_preview: typing.Callable[[], None],
        request_transcript_backtrack: typing.Callable[
            [TranscriptBacktrackRequest],
            None,
        ],
        report_missing_transcript_backtrack: typing.Callable[[], None],
        export_transcript: typing.Callable[
            [typing.Iterable[TranscriptBlock], TranscriptExportFormat],
            TranscriptExportResult,
        ] | None,
        observe_terminal_geometry: typing.Callable[[int, int], None],
        observe_render_revision: typing.Callable[[int], None],
        keymap: TuiRuntimeKeymap,
        input_obj: Input | None = None,
        output_obj: Output | None = None,
        terminal_capabilities: TerminalCapabilities = (
            DEGRADED_TERMINAL_CAPABILITIES
        ),
        open_static_pager: typing.Callable[[StaticPagerRequest], bool] | None = None,
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

        self._can_transcript_backtrack     = can_transcript_backtrack
        self._clear_exit_confirmation      = clear_exit_confirmation
        self._clear_visible_transcript     = clear_visible_transcript
        self._scroll_transcript_page       = scroll_transcript_page
        self._toggle_transcript_overlay    = toggle_transcript_overlay
        self._close_mailbox_overlay        = close_mailbox_overlay
        self._close_static_pager           = close_static_pager
        self._open_static_pager            = open_static_pager
        self._request_resume_preview       = request_resume_preview
        self._request_resume_transcript    = request_resume_transcript
        self._cancel_resume_preview        = cancel_resume_preview
        self._request_transcript_backtrack = request_transcript_backtrack

        self._report_missing_transcript_backtrack = (
            report_missing_transcript_backtrack
        )

        self._export_transcript = export_transcript

        self._observe_terminal_geometry = observe_terminal_geometry
        self._observe_render_revision   = observe_render_revision

        self.keymap: TuiRuntimeKeymap = keymap

        self._validate_keymap(keymap)

        self._startup_gate_active: bool     = False
        self._startup_surface_cleared: bool = False

        self._clear_for_viewport_change_pending: bool = False

        self._visual_update_depth: int  = 0
        self._visual_update_dirty: bool = False

        self._synchronized_output_depth: int   = 0
        self._synchronized_frame_pending: bool = False
        self._synchronized_frame_active: bool  = False

        self._frame_geometry: FrameGeometry | None = None
        self._frame_output_size: Size | None       = None

        self._rendered_output_size: Size | None = None

        self._bottom_pane_frame_layout: BottomPaneLayout | None = None

        self._transcript_only: bool                                 = False
        self._transcript_cache_key: tuple[int, int, int] | None     = None
        self._transcript_cache_fragments: FormattedText             = []
        self._transcript_assistant_lines: frozenset[int]            = frozenset()
        self._transcript_text_key: tuple[int, int, int] | None      = None
        self._transcript_metrics_key: tuple[int, int, int] | None   = None
        self._transcript_cache_continuation_widths: tuple[int, ...] = ()
        self._transcript_cache_text: str                            = ""
        self._transcript_cache_display_rows: int                    = 0

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

        self.input_prompt_control = FormattedTextControl(
            self._input_prompt_fragments,
        )
        self.input_prompt_window = Window(
            content=self.input_prompt_control,
            width=Dimension.exact(self.INPUT_TEXT_LEFT_MARGIN),
            height=self._input_dimension,
            dont_extend_width=True,
            dont_extend_height=True,
            style="class:input-surface",
        )
        self.input_editor = VSplit(
            [self.input_prompt_window, self.input],
            height=self._input_dimension,
            window_too_small=self.input.window,
        )

        self.input.buffer.enable_history_search = to_filter(True)

        self.input.buffer.on_text_changed += on_input_text_changed
        self.input.buffer.on_text_changed += (
            self.input_model.reopen_completion_menu
        )
        self.input.buffer.on_text_insert += (
            self.input_model.refresh_inserted_completion_menu
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

        self.directory_trust = TuiDirectoryTrust(
            invalidate=self.invalidate,
            focus_prompt=self._focus_directory_trust,
            focus_input=self._focus_input,
            get_width=lambda: self.terminal_width,
            get_max_height=lambda: self.terminal_height,
        )
        self.directory_trust_control = FormattedTextControl(
            self.directory_trust.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.directory_trust.key_bindings,
        )

        self.approval = TuiApproval(
            invalidate=self.invalidate,
            focus_card=lambda: self._activate_bottom_surface("approval"),
            focus_input=lambda: self._deactivate_bottom_surface("approval"),
            get_width=lambda: self.terminal_width,
            get_max_height=self._active_view_available_height,
            terminal_capabilities=terminal_capabilities,
            open_static_pager=self._open_approval_pager,
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
            focus_menu=lambda: self._activate_bottom_surface("menu"),
            focus_input=lambda: self._deactivate_bottom_surface("menu"),
            get_width=lambda: self.terminal_width,
            view_stack=self.bottom_pane.view_stack,
        )
        self.menu_control = FormattedTextControl(
            self._menu_view_fragments,
            focusable=True,
            modal=True,
            key_bindings=self.menu.key_bindings,
        )
        self.menu_footer_control = FormattedTextControl(
            self._menu_footer_fragments,
        )
        self.startup_menu_control = FormattedTextControl(
            self._menu_view_fragments,
            focusable=True,
            modal=True,
            key_bindings=self.menu.key_bindings,
        )
        self.startup_menu_footer_control = FormattedTextControl(
            self._menu_footer_fragments,
        )
        self.transcript_overlay = TuiTranscriptOverlay(
            document=self.document,
            get_width=lambda: self.terminal_width,
            get_height=lambda: self._transcript_overlay_height(),
            get_snapshot=self.document.transcript_snapshot,
            invalidate=self.invalidate,
        )
        self.mailbox_overlay = TuiMailboxOverlay(
            get_width=lambda: self.terminal_width,
            get_height=lambda: self._mailbox_overlay_height(),
            invalidate=self.invalidate,
        )
        self.static_pager = TuiStaticPager(
            get_width=lambda: self.terminal_width,
            get_height=lambda: self._static_pager_height(),
            invalidate=self.invalidate,
        )
        self.resume_picker = TuiResumePicker(
            invalidate=self.invalidate,
            get_width=lambda: self.terminal_width,
            get_height=lambda: self.terminal_height,
            request_preview=self._request_resume_preview,
            request_transcript=self._request_resume_transcript,
            cancel_preview=self._cancel_resume_preview,
        )
        self.transcript_overlay_control = FormattedTextControl(
            self.transcript_overlay.visible_fragments,
            focusable=True,
            modal=True,
            key_bindings=self._transcript_overlay_key_bindings(),
        )
        self.mailbox_overlay_control = FormattedTextControl(
            self.mailbox_overlay.visible_fragments,
            focusable=True,
            modal=True,
            key_bindings=self._mailbox_overlay_key_bindings(),
        )
        self.static_pager_control = FormattedTextControl(
            self.static_pager.visible_fragments,
            focusable=True,
            modal=True,
            key_bindings=self._static_pager_key_bindings(),
        )
        self.resume_picker_control = FormattedTextControl(
            self.resume_picker.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.resume_picker.key_bindings,
        )

        self.transcript_window = TerminalHyperlinkWindow(
            content=self.transcript_control,
            height=self._transcript_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
            get_line_prefix=self._transcript_line_prefix,
        )
        self.transcript_overlay_window = TerminalHyperlinkWindow(
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
        self.mailbox_overlay_window = Window(
            content=self.mailbox_overlay_control,
            height=self._mailbox_overlay_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
            char=" ",
        )
        self.mailbox_overlay_header = Window(
            content=FormattedTextControl(
                self._mailbox_overlay_header_fragments
            ),
            height=self._mailbox_overlay_header_dimension,
            dont_extend_height=True,
            char=" ",
        )
        self.mailbox_overlay_footer = HSplit(
            [
                Window(
                    content=FormattedTextControl(
                        self._mailbox_overlay_separator_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    content=FormattedTextControl(
                        self._mailbox_overlay_primary_help_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    content=FormattedTextControl(
                        self._mailbox_overlay_secondary_help_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
            ],
            height=self._mailbox_overlay_footer_dimension,
            window_too_small=Window(),
        )
        self.static_pager_window = TerminalHyperlinkWindow(
            content=self.static_pager_control,
            height=self._static_pager_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
            char=" ",
        )
        self.static_pager_header = Window(
            content=FormattedTextControl(
                self._static_pager_header_fragments
            ),
            height=self._static_pager_header_dimension,
            dont_extend_height=True,
            char=" ",
        )
        self.static_pager_footer = HSplit(
            [
                Window(
                    content=FormattedTextControl(
                        self._static_pager_separator_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    content=FormattedTextControl(
                        self._static_pager_primary_help_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    content=FormattedTextControl(
                        self._static_pager_secondary_help_fragments
                    ),
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
                Window(
                    height=Dimension.exact(1),
                    dont_extend_height=True,
                    char=" ",
                ),
            ],
            height=self._static_pager_footer_dimension,
            window_too_small=Window(),
        )
        self.resume_picker_window = Window(
            content=self.resume_picker_control,
            height=self._resume_picker_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
            char=" ",
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
            style="class:menu-card",
            char=" ",
        )
        self.menu_top_padding = Window(
            height=self._menu_top_padding_dimension,
            char=" ",
            style="class:menu-card",
            dont_extend_height=True,
        )
        self.menu_bottom_padding = Window(
            height=self._menu_bottom_padding_dimension,
            char=" ",
            style="class:menu-card",
            dont_extend_height=True,
        )
        self.menu_footer_window = Window(
            content=self.menu_footer_control,
            height=self._menu_footer_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.startup_menu_window = Window(
            content=self.startup_menu_control,
            height=self._startup_menu_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
            style="class:menu-card",
            char=" ",
        )
        self.startup_menu_top_padding = Window(
            height=self._menu_top_padding_dimension,
            char=" ",
            style="class:menu-card",
            dont_extend_height=True,
        )
        self.startup_menu_gap = Window(
            height=self._menu_bottom_padding_dimension,
            char=" ",
            style="class:menu-card",
            dont_extend_height=True,
        )
        self.startup_menu_footer_window = Window(
            content=self.startup_menu_footer_control,
            height=self._menu_footer_dimension,
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
        self.directory_trust_window = Window(
            content=self.directory_trust_control,
            height=self._directory_trust_dimension,
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
            HSplit(
                [
                    self.menu_top_padding,
                    self.menu_window,
                    self.menu_bottom_padding,
                ],
                align=VerticalAlign.TOP,
                window_too_small=Window(),
            ),
            filter=Condition(lambda: self.bottom_pane.is_active("menu")),
        )
        self.menu_footer = ConditionalContainer(
            self.menu_footer_window,
            filter=Condition(lambda: self.bottom_pane.is_active("menu")),
        )
        self.active_view_area = ConditionalContainer(
            HSplit(
                [
                    self.approval_card,
                    self.menu_card,
                    self.menu_footer,
                ],
                align=VerticalAlign.TOP,
                height=self._active_view_dimension,
                window_too_small=Window(),
            ),
            filter=Condition(lambda: (
                self.bottom_pane.transient_active
                and not self._transcript_only
            )),
        )
        self.bottom_pane_top_inset = ConditionalContainer(
            Window(height=self._bottom_pane_top_inset_dimension, char=" "),
            filter=Condition(self._bottom_pane_top_inset_visible),
        )
        self.status_interaction_gap = ConditionalContainer(
            Window(height=self._status_interaction_gap_dimension, char=" "),
            filter=Condition(self._status_interaction_gap_visible),
        )
        self.completion_menu = Window(
            content=TokenCompletionMenuControl(
                lambda: self.input_model.token_menu_snapshot(
                    self.input.buffer
                )
            ),
            width=Dimension(min=8),
            height=Dimension(min=1, max=self.COMPLETION_MAX_HEIGHT),
            scroll_offsets=ScrollOffsets(top=1, bottom=1),
            dont_extend_width=True,
            style="class:token-menu",
        )

        self.completion_menu_hint_spacer_window = Window(
            height=Dimension.exact(1),
            char=" ",
            style="class:token-menu",
        )
        self.completion_menu_hint_spacer = ConditionalContainer(
            self.completion_menu_hint_spacer_window,
            filter=Condition(self._completion_hint_visible),
        )

        self.completion_menu_hint_window = Window(
            content=FormattedTextControl(self._completion_hint_fragments),
            height=Dimension.exact(1),
            dont_extend_width=True,
            style="class:token-menu",
        )
        self.completion_menu_hint = ConditionalContainer(
            self.completion_menu_hint_window,
            filter=Condition(self._completion_hint_visible),
        )

        self.completion_menu_row = ConditionalContainer(
            self.completion_menu,
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
            self.completion_fallback_window,
            filter=Condition(self._completion_fallback_visible),
        )

        self.input_surface = HSplit(
            [
                self.input_top_padding,
                self.input_editor,
                self.input_bottom_padding,
            ],
            align=VerticalAlign.TOP,
            height=self._input_surface_dimension,
            window_too_small=Window(),
        )
        self.compact_input_surface = ConditionalContainer(
            HSplit(
                [self.input_editor],
                align=VerticalAlign.BOTTOM,
                window_too_small=self.input_editor,
            ),
            filter=Condition(lambda: bool(
                getattr(self, "application", None)
                and self.application.is_running
            )),
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
                self.completion_menu_hint_spacer,
                self.completion_menu_hint,
                self.input_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._input_stack_dimension,
            window_too_small=self.compact_input_surface,
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
        self.bottom_pane_area = ConditionalContainer(
            HSplit(
                [
                    self.status_window,
                    self.process_status_window,
                    self.queued_window,
                    self.status_interaction_gap,
                    self.active_view_area,
                    self.input_area,
                ],
                align=VerticalAlign.TOP,
                height=self._bottom_pane_content_dimension,
                window_too_small=Window(),
            ),
            filter=Condition(self._bottom_pane_visible),
        )
        self.canvas_spacer = Window(
            height=Dimension(min=0, preferred=0, weight=1),
            char=" ",
        )

        self.canvas = HSplit(
            [
                self.canvas_spacer,
                self.transcript_window,
                self.bottom_pane_top_inset,
                self.bottom_pane_area,
            ],
            align=VerticalAlign.JUSTIFY,
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
        self.mailbox_overlay_canvas = HSplit(
            [
                self.mailbox_overlay_header,
                self.mailbox_overlay_window,
                self.mailbox_overlay_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._mailbox_overlay_canvas_dimension,
            window_too_small=Window(),
        )
        self.static_pager_canvas = HSplit(
            [
                self.static_pager_header,
                self.static_pager_window,
                self.static_pager_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._static_pager_canvas_dimension,
            window_too_small=Window(),
        )
        self.resume_picker_canvas = HSplit(
            [self.resume_picker_window],
            align=VerticalAlign.TOP,
            height=self._resume_picker_dimension,
            window_too_small=Window(),
        )
        self.directory_trust_canvas = HSplit(
            [self.directory_trust_window],
            align=VerticalAlign.TOP,
            height=self._directory_trust_dimension,
            window_too_small=Window(),
        )
        self.startup_canvas = HSplit(
            [
                self.startup_menu_top_padding,
                self.startup_menu_window,
                self.startup_menu_gap,
                self.startup_menu_footer_window,
            ],
            align=VerticalAlign.TOP,
            height=self._startup_dimension,
            window_too_small=Window(),
        )
        self.root = HSplit(
            [
                ConditionalContainer(
                    self.canvas,
                    filter=Condition(lambda: (
                        not self._startup_gate_active
                        and not self.transcript_overlay.active
                        and not self.mailbox_overlay.active
                        and not self.static_pager.active
                        and not self.resume_picker.active
                        and not self.directory_trust.active
                    )),
                ),
                ConditionalContainer(
                    self.transcript_overlay_canvas,
                    filter=Condition(lambda: (
                        not self._startup_gate_active
                        and self.transcript_overlay.active
                        and not self.mailbox_overlay.active
                        and not self.static_pager.active
                        and not self.resume_picker.active
                        and not self.directory_trust.active
                    )),
                ),
                ConditionalContainer(
                    self.mailbox_overlay_canvas,
                    filter=Condition(lambda: (
                        not self._startup_gate_active
                        and self.mailbox_overlay.active
                        and not self.transcript_overlay.active
                        and not self.static_pager.active
                        and not self.resume_picker.active
                        and not self.directory_trust.active
                    )),
                ),
                ConditionalContainer(
                    self.static_pager_canvas,
                    filter=Condition(lambda: (
                        not self._startup_gate_active
                        and self.static_pager.active
                        and not self.transcript_overlay.active
                        and not self.mailbox_overlay.active
                        and not self.resume_picker.active
                        and not self.directory_trust.active
                    )),
                ),
                ConditionalContainer(
                    self.resume_picker_canvas,
                    filter=Condition(lambda: (
                        not self._startup_gate_active
                        and self.resume_picker.active
                        and not self.transcript_overlay.active
                        and not self.mailbox_overlay.active
                        and not self.static_pager.active
                        and not self.directory_trust.active
                    )),
                ),
                ConditionalContainer(
                    self.directory_trust_canvas,
                    filter=Condition(lambda: self.directory_trust.active),
                ),
                ConditionalContainer(
                    self.startup_canvas,
                    filter=Condition(lambda: (
                        self._startup_gate_active
                        and not self.directory_trust.active
                        and not self.transcript_overlay.active
                        and not self.mailbox_overlay.active
                        and not self.static_pager.active
                        and not self.resume_picker.active
                    )),
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
            before_render=self._prepare_frame_render,
            after_render=self._finish_frame_render,
            input=application_input,
            output=application_output,
        )

        self.hyperlinks_enabled = bool(
            terminal_capabilities.hyperlinks
            and _supports_vt_control(self.application.output)
        )
        if self.hyperlinks_enabled:
            hyperlink_output = TerminalHyperlinkOutput(
                self.application.output
            )
            self.application.output = hyperlink_output
            self.application.renderer.output = hyperlink_output

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

    @property
    def startup_gate_active(self) -> bool:
        """返回启动阶段是否仍隐藏主输入画布。"""
        return self._startup_gate_active

    @staticmethod
    def _transcript_continuation_widths(
        fragments: FormattedText
    ) -> tuple[int, ...]:
        """返回正文每个逻辑行的自动折行前缀宽度。"""
        return fragment_continuation_widths(
            fragments,
            prefix_style=ASSISTANT_PREFIX_CLASS,
            prefix_width=2,
        )

    @staticmethod
    def _assistant_lines(fragments: FormattedText) -> frozenset[int]:
        """返回属于助手正文块的全部逻辑行索引。"""
        assistant_lines: set[int] = set()

        line_number: int    = 0
        at_line_start: bool = True

        for style, text in fragments:
            parts      = text.split("\n")
            last_index = len(parts) - 1

            for index, part in enumerate(parts):
                if at_line_start and part:
                    if style == ASSISTANT_PREFIX_CLASS:
                        assistant_lines.add(line_number)
                    at_line_start = False

                if index < last_index:
                    line_number += 1
                    at_line_start = True

        return frozenset(assistant_lines)

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
    def _add_configured_bindings(
        bindings: KeyBindings,
        configured: tuple[TuiKeyBinding, ...],
        handler: typing.Callable[[typing.Any], None],
        *,
        binding_filter: typing.Any = True
    ) -> None:
        """把已解析的按键序列注册到一个输入上下文。"""
        for binding in configured:
            bindings.add(
                *binding.keys,
                eager=True,
                filter=binding_filter,
            )(handler)

    @staticmethod
    def _help_line(hints: typing.Iterable[str]) -> str:
        """组合一行非空的完整记录操作提示。"""
        content = "   ".join(hint for hint in hints if hint)
        return f" {content}" if content else ""

    def invalidate(self) -> None:
        """请求重新绘制当前稳定画布。"""
        if self._visual_update_depth:
            self._visual_update_dirty = True
            return None

        self._invalidate_now()

    def output_geometry(self) -> tuple[int, int]:
        """返回物理输出使用的终端列数和行数。"""
        size = (
            self._frame_output_size
            if self._frame_geometry is not None
            else None
        )
        if size is None:
            width, height = self._output_size()
        else:
            width, height = size.columns, size.rows
        return max(20, width), max(1, height)

    def visual_update(self) -> contextlib.AbstractContextManager[None]:
        """把一组同步画面状态变更合并为一次绘制请求。"""

        @contextlib.contextmanager
        def transaction() -> typing.Iterator[None]:
            self._visual_update_depth += 1
            try:
                yield
            finally:
                self._visual_update_depth -= 1
                if (
                    self._visual_update_depth == 0
                    and self._visual_update_dirty
                ):
                    self._visual_update_dirty = False
                    self._invalidate_now()

        return transaction()

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

    def set_transcript_only(self, active: bool) -> None:
        """切换为只保留正文的终端画布。"""
        self._transcript_only = bool(active)
        self.invalidate()

    def set_startup_gate(self, active: bool) -> None:
        """切换启动阶段独占画布并路由键盘焦点。"""
        active = bool(active)
        if active == self._startup_gate_active:
            return None

        self._startup_gate_active = active
        if active:
            self._startup_surface_cleared = False
            self.application.layout.focus(self.startup_menu_control)
        else:
            self._focus_input()
        self.invalidate()

    def clear_startup_surface(self) -> None:
        """清理启动菜单首次出现前残留的终端画面。"""
        if self._startup_surface_cleared:
            return None
        self._startup_surface_cleared = True
        self.application.renderer.clear()

    def clear_for_viewport_change(self) -> None:
        """请求在启动表面切换后的下一帧清理视口残留。"""
        if not self._startup_surface_cleared:
            return None
        self._clear_for_viewport_change_pending = True
        self.synchronize_next_render()

    def refresh_input_layout(self) -> None:
        """按当前输入和补全状态请求重新计算布局。"""
        self.invalidate()

    def set_transcript_overlay(self, active: bool) -> bool:
        """切换完整会话记录、终端画面和键盘焦点。"""
        active = bool(active)
        if active == self.transcript_overlay.active:
            return False
        if active and (
            self.mailbox_overlay.active
            or self.static_pager.active
            or self.resume_picker.active
            or self._full_screen_overlay_blocked()
        ):
            return False

        if active:
            try:
                self._enter_full_screen_overlay()
                self.transcript_overlay.open()
                self.application.layout.focus(self.transcript_overlay_control)
            except BaseException:
                self.transcript_overlay.active = False
                try:
                    self._leave_full_screen_overlay()
                finally:
                    self._restore_overlay_focus()
                raise
        else:
            try:
                self.transcript_overlay.close()
            finally:
                try:
                    self._leave_full_screen_overlay()
                finally:
                    self._restore_overlay_focus()
        self.invalidate()
        return True

    def set_mailbox_entries(
        self,
        entries: typing.Iterable[MailboxEntry],
        *,
        listener_active: bool
    ) -> bool:
        """更新收件箱快照并在内容变化时刷新当前画面。"""
        previous_count = self.mailbox_overlay.pending_count
        changed = self.mailbox_overlay.update(
            entries,
            listener_active=listener_active,
        )
        if (
            changed
            and not self.mailbox_overlay.active
            and previous_count != self.mailbox_overlay.pending_count
        ):
            self.invalidate()
        return changed

    def set_mailbox_overlay(
        self,
        active: bool,
        *,
        entry_key: str | None = None,
        allow_menu: bool = False,
    ) -> bool:
        """切换全屏收件箱、终端画面和键盘焦点。"""
        active = bool(active)
        if active == self.mailbox_overlay.active:
            return False
        menu_only = allow_menu and self.bottom_pane.is_active("menu")
        if active and (
            self.transcript_overlay.active
            or self.static_pager.active
            or self.resume_picker.active
            or (
                self._full_screen_overlay_blocked()
                and not menu_only
            )
        ):
            return False

        if active:
            try:
                self._enter_full_screen_overlay()
                if not self.mailbox_overlay.open(entry_key or ""):
                    self._leave_full_screen_overlay()
                    self._restore_overlay_focus()
                    return False
                self.application.layout.focus(self.mailbox_overlay_control)
            except BaseException:
                self.mailbox_overlay.abort()
                try:
                    self._leave_full_screen_overlay()
                finally:
                    self._restore_overlay_focus()
                raise
        else:
            try:
                self.mailbox_overlay.close()
            finally:
                try:
                    self._leave_full_screen_overlay()
                finally:
                    self._restore_overlay_focus()
        self.invalidate()
        return True

    def set_static_pager(
        self,
        active: bool,
        *,
        request: StaticPagerRequest | None = None,
        allow_approval: bool = False,
    ) -> bool:
        """切换静态 pager、终端画面和键盘焦点。"""
        active = bool(active)
        if active == self.static_pager.active:
            return False
        if active and (
            self.transcript_overlay.active
            or self.mailbox_overlay.active
            or self.resume_picker.active
            or self._full_screen_overlay_blocked(
                allow_approval=allow_approval,
            )
        ):
            return False
        if active and request is None:
            raise ValueError("static pager request is required")

        if active:
            try:
                self._enter_full_screen_overlay()
                self.static_pager.open(request)
                self.application.layout.focus(self.static_pager_control)
            except BaseException:
                self.static_pager.abort()
                try:
                    self._leave_full_screen_overlay()
                finally:
                    self._restore_overlay_focus()
                raise
        else:
            try:
                self.static_pager.close()
            finally:
                try:
                    self._leave_full_screen_overlay()
                finally:
                    self._restore_overlay_focus()
        self.invalidate()
        return True

    def _open_approval_pager(self, request: StaticPagerRequest) -> bool:
        """从审批面板打开命令的只读全屏预览。"""
        if self._open_static_pager is not None:
            return self._open_static_pager(request)
        return self.set_static_pager(
            True,
            request=request,
            allow_approval=True,
        )

    def set_resume_picker(
        self,
        active: bool,
        *,
        request: ResumePickerRequest | None = None,
        generation: int = 0,
    ) -> bool:
        """切换全屏 Resume picker、终端画面和键盘焦点。"""
        active = bool(active)
        if active == self.resume_picker.active:
            return False
        if active and (
            self.transcript_overlay.active
            or self.mailbox_overlay.active
            or self.static_pager.active
            or self._full_screen_overlay_blocked()
        ):
            return False
        if active and request is None:
            raise ValueError("resume picker request is required")

        if active:
            try:
                self._enter_full_screen_overlay()
                if not self.resume_picker.open(
                    request,
                    generation=generation,
                ):
                    self._leave_full_screen_overlay()
                    self._restore_overlay_focus()
                    return False
                self.application.layout.focus(self.resume_picker_control)
            except BaseException:
                try:
                    self.resume_picker.close()
                finally:
                    try:
                        self._leave_full_screen_overlay()
                    finally:
                        self._restore_overlay_focus()
                raise
        else:
            try:
                self.resume_picker.close()
            finally:
                try:
                    self._leave_full_screen_overlay()
                finally:
                    self._restore_overlay_focus()
        self.invalidate()
        return True

    async def wait_resume_picker(self) -> ResumePickerResult:
        """等待当前 Resume picker 返回选择或取消。"""
        return await self.resume_picker.wait()

    def set_resume_preview(
        self,
        preview: ResumePreview,
        *,
        generation: int,
    ) -> bool:
        """提交属于当前 picker generation 的 preview 结果。"""
        return self.resume_picker.update_preview(
            preview,
            generation=generation,
        )

    def set_activity_renderable(self, block: FragmentBlock) -> None:
        """替换活动状态区域的展示内容。"""
        self.activity_block = block
        if not self._full_screen_overlay_active():
            self.invalidate()

    def clear_activity_renderable(self) -> None:
        """清空活动状态区域的展示内容。"""
        changed = self.activity_block is not None
        self.activity_block = None
        if changed and not self._full_screen_overlay_active():
            self.invalidate()

    def clear_terminal_scrollback(self) -> None:
        """清除当前画布及终端滚屏缓冲区。"""
        self.application.renderer.clear()
        _erase_terminal_scrollback(self.application.output)
        self.invalidate()

    def clear_terminal_for_resize_replay(self) -> None:
        """在尺寸重排事务中清除可见画面和原生滚屏。"""
        _clear_terminal_for_resize_replay(self.application.output)

    def begin_synchronized_output(self) -> bool:
        """开始终端同步输出更新并返回是否已启用。"""
        if self._synchronized_output_depth:
            self._synchronized_output_depth += 1
            return True

        if not _set_synchronized_output(self.application.output, True):
            return False

        self._synchronized_output_depth = 1
        return True

    def end_synchronized_output(self) -> None:
        """结束终端同步输出更新。"""
        if self._synchronized_output_depth <= 0:
            return None

        self._synchronized_output_depth -= 1
        if self._synchronized_output_depth == 0:
            _set_synchronized_output(self.application.output, False)

    def reset_synchronized_output(self) -> None:
        """释放尚未结束的终端同步输出状态。"""
        self._synchronized_frame_pending = False
        self._synchronized_frame_active  = False

        if self._synchronized_output_depth <= 0:
            return None

        self._synchronized_output_depth = 0
        _set_synchronized_output(self.application.output, False)

    def synchronize_next_render(self) -> None:
        """请求把下一帧作为一次终端同步更新提交。"""
        self._synchronized_frame_pending = True
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
        width = self.terminal_width

        key = (
            self.document.transcript_revision,
            self.document.visible_prefix_line_count,
            width,
        )
        if key != self._transcript_cache_key:
            fragments = self.document.fragments(
                width=width,
                reflow_sources=False,
            )
            self._transcript_cache_key = key
            self._transcript_cache_fragments = fragments
            self._transcript_assistant_lines = self._assistant_lines(fragments)

        return self._transcript_cache_fragments

    def transcript_available_height(self) -> int:
        """估算首帧渲染前正文可使用的终端行数。"""
        return max(
            0,
            self.terminal_height
            - self._bottom_pane_layout().total_height,
        )

    def _invalidate_now(self) -> None:
        """立即向运行中的 Application 提交一次绘制请求。"""
        application = getattr(self, "application", None)
        if (
            application is not None
            and application.is_running
            and not application.is_done
        ):
            with contextlib.suppress(Exception):
                application.invalidate()

    def _transcript_text(self) -> str:
        """返回当前正文版本可复用的纯文本。"""
        fragments = self.transcript_fragments()
        key       = self._transcript_cache_key

        if key != self._transcript_text_key:
            self._transcript_text_key = key
            self._transcript_cache_text = fragments_text(fragments)

        return self._transcript_cache_text

    def _transcript_display_metrics(
        self,
    ) -> tuple[tuple[int, ...], int]:
        """返回当前正文版本可复用的续行宽度与显示行数。"""
        fragments = self.transcript_fragments()
        key       = self._transcript_cache_key

        if key != self._transcript_metrics_key:
            text = self._transcript_text()
            continuation_widths = self._transcript_continuation_widths(
                fragments,
            )

            self._transcript_metrics_key = key
            self._transcript_cache_continuation_widths = continuation_widths
            self._transcript_cache_display_rows = display_line_count(
                text,
                width=self.terminal_width,
                continuation_widths=continuation_widths,
            )

        return (
            self._transcript_cache_continuation_widths,
            self._transcript_cache_display_rows,
        )

    def _prepare_frame_render(self, application: Application[None]) -> None:
        """开始同步帧并固定本次布局计算使用的终端尺寸。"""
        if self._synchronized_frame_pending:
            self._synchronized_frame_pending = False
            self._synchronized_frame_active = (
                self.begin_synchronized_output()
            )

        try:
            self._capture_frame_geometry(application)
            if self._clear_for_viewport_change_pending:
                self._clear_for_viewport_change_pending = False
                self.application.renderer.clear()
            elif (
                self._startup_gate_active
                and self.bottom_pane.is_active("menu")
            ):
                self.clear_startup_surface()
        except BaseException:
            self._finish_synchronized_frame()
            raise

    def _finish_frame_render(self, application: Application[None]) -> None:
        """完成帧状态记录并释放本次终端同步更新。"""
        try:
            self._release_frame_geometry(application)
        finally:
            self._finish_synchronized_frame()

    def _finish_synchronized_frame(self) -> None:
        """结束当前帧持有的终端同步输出状态。"""
        if not self._synchronized_frame_active:
            return None
        self._synchronized_frame_active = False
        self.end_synchronized_output()

    def _capture_frame_geometry(self, application: Application[None]) -> None:
        """在布局计算前固定当前帧使用的终端尺寸。"""
        self._bottom_pane_frame_layout = None

        self._frame_geometry = self._read_frame_geometry(
            revision=application.render_counter,
        )

        geometry = self.output_geometry()

        self.document.set_display_width(
            self._frame_geometry.width,
            reflow_sources=False,
        )

        self._observe_terminal_geometry(*geometry)

    def _release_frame_geometry(self, application: Application[None]) -> None:
        """在渲染结束后恢复终端尺寸的实时读取。"""
        self._observe_render_revision(application.render_counter)
        self._rendered_output_size = self._frame_output_size

        self._frame_output_size = None
        self._frame_geometry    = None

        self._bottom_pane_frame_layout = None

    def _read_frame_geometry(self, *, revision: int) -> FrameGeometry:
        """读取并规范化一个终端尺寸快照。"""
        width, height = self._output_size()
        self._frame_output_size = Size(rows=height, columns=width)

        height = self._inline_frame_height(
            width=width,
            height=height,
        )

        return FrameGeometry(
            width=max(20, width),
            height=max(1, height),
            revision=max(0, int(revision)),
        )

    def _inline_frame_height(self, *, width: int, height: int) -> int:
        """根据动态内容决定 inline 布局是否使用完整终端高度。"""
        application = getattr(self, "application", None)
        if application is None or application.full_screen:
            return height
        if (
            self._inline_reply_handoff_growth_active()
            or self._inline_active_transcript_growth_active()
            or self._input_auxiliary_height(width=width) > 0
            or self._inline_completion_growth_active()
            or self._inline_input_growth_active(width=width)
            or self.bottom_pane.transient_active
        ):
            return height

        renderer        = application.renderer
        previous_screen = renderer.last_rendered_screen
        output_size     = Size(rows=height, columns=width)

        if (
            previous_screen is None
            or self._rendered_output_size != output_size
            or not renderer.height_is_known
        ):
            return height

        available_height = height - renderer.rows_above_layout
        return min(height, max(1, available_height))

    def _restore_overlay_focus(self) -> None:
        """把全屏覆盖层关闭后的焦点恢复到当前交互表面。"""
        surface = self.bottom_pane.active_surface
        if surface is None:
            self._focus_input()
        else:
            self._focus_bottom_surface(surface)

    def _enter_full_screen_overlay(self) -> None:
        """保存 inline 渲染状态并准备全屏覆盖画面。"""
        renderer = self.application.renderer
        if self._inline_renderer_state is not None:
            return None

        terminal_height = self.terminal_height

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

        self.application.full_screen  = True
        renderer.full_screen          = True
        renderer._in_alternate_screen = True

        renderer.output.enter_alternate_screen()
        _set_alternate_scroll_mode(renderer.output, True)
        renderer.output.erase_screen()
        renderer.output.cursor_goto(0, 0)
        renderer.output.flush()

        renderer._cursor_pos = Point(x=0, y=0)

        renderer._last_screen       = None
        renderer._last_size         = None
        renderer._last_style        = None
        renderer._last_cursor_shape = None

        renderer._min_available_height = terminal_height

    def _leave_full_screen_overlay(self) -> None:
        """退出全屏覆盖画面并恢复 inline 渲染状态。"""
        renderer = self.application.renderer
        state    = self._inline_renderer_state

        try:
            if renderer._in_alternate_screen:
                try:
                    _set_alternate_scroll_mode(renderer.output, False)
                finally:
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

    def _focus_bottom_surface(self, surface: BottomSurface) -> None:
        """把焦点切换到指定的底部临时交互表面。"""
        self._clear_exit_confirmation()

        if self.directory_trust.active:
            self._focus_directory_trust()
            return None

        if (
            hasattr(self, "resume_picker")
            and self.resume_picker.active
        ):
            self.application.layout.focus(self.resume_picker_control)
            return None

        if (
            hasattr(self, "transcript_overlay")
            and self.transcript_overlay.active
        ):
            self.application.layout.focus(self.transcript_overlay_control)
            return None
        if (
            hasattr(self, "mailbox_overlay")
            and self.mailbox_overlay.active
        ):
            self.application.layout.focus(self.mailbox_overlay_control)
            return None
        if (
            hasattr(self, "static_pager")
            and self.static_pager.active
        ):
            self.application.layout.focus(self.static_pager_control)
            return None

        controls = {
            "approval": self.approval_control,
            "menu": (
                self.startup_menu_control
                if self._startup_gate_active
                else self.menu_control
            ),
        }
        self.application.layout.focus(controls[surface])

    def _activate_bottom_surface(self, surface: BottomSurface) -> None:
        """激活底部临时表面并切换焦点。"""
        if self._startup_gate_active and surface == "menu":
            self.synchronize_next_render()
        self.bottom_pane.activate(surface)
        if surface == "menu" and hasattr(self, "menu_window"):
            self._sync_menu_surface_style()

    def _deactivate_bottom_surface(self, surface: BottomSurface) -> None:
        """撤下底部临时表面并按当前表面重新计算布局。"""
        self.bottom_pane.deactivate(surface)
        if surface == "menu" and hasattr(self, "menu_window"):
            self._sync_menu_surface_style()

    def _focus_input(self) -> None:
        """把焦点路由到当前顶层记录或主输入控件。"""
        if self.directory_trust.active:
            self._focus_directory_trust()
            return None

        if self._startup_gate_active:
            self.application.layout.focus(self.startup_menu_control)
            return None

        if (
            hasattr(self, "resume_picker")
            and self.resume_picker.active
        ):
            self.application.layout.focus(self.resume_picker_control)
            return None

        if (
            hasattr(self, "transcript_overlay")
            and self.transcript_overlay.active
        ):
            self.application.layout.focus(self.transcript_overlay_control)
            return None
        if (
            hasattr(self, "mailbox_overlay")
            and self.mailbox_overlay.active
        ):
            self.application.layout.focus(self.mailbox_overlay_control)
            return None
        if (
            hasattr(self, "static_pager")
            and self.static_pager.active
        ):
            self.application.layout.focus(self.static_pager_control)
            return None

        self.application.layout.focus(self.input)

    def _focus_directory_trust(self) -> None:
        """把焦点路由到启动阶段的目录信任界面。"""
        application = getattr(self, "application", None)
        if application is not None:
            application.layout.focus(self.directory_trust_control)

    def _input_prompt_fragments(self) -> StyleAndTextTuples:
        """生成输入区域首行的独立模式提示符。"""
        return input_prompt_fragments(
            shell_mode=self.input_model.shell_mode,
        )

    def _transcript_line_prefix(
        self,
        line_number: int,
        wrap_count: int
    ) -> StyleAndTextTuples:
        """让助手正文自动折行后继续与首行正文对齐。"""
        if (
            wrap_count <= 0
            or line_number not in self._transcript_assistant_lines_for_frame()
        ):
            return []

        return [(ASSISTANT_PREFIX_CLASS, "  ")]

    def _transcript_assistant_lines_for_frame(self) -> frozenset[int]:
        """返回当前正文版本缓存的助手逻辑行索引。"""
        self.transcript_fragments()
        return self._transcript_assistant_lines

    def _transcript_fragments(self) -> FormattedText:
        """返回正文控件使用的格式化片段。"""
        return self.transcript_fragments()

    def _placeholder_fragments(self) -> StyleAndTextTuples:
        """返回当前输入轮次固定的占位文案。"""
        return placeholder_fragments(self._get_placeholder_text())

    def _status_fragments(self) -> FormattedText:
        """生成动画专属区域的格式化片段。"""
        block = self.activity_block
        if block is None:
            return []

        width = (
            self._frame_output_size.columns
            if self._frame_output_size is not None
            else self._output_size()[0]
        )

        activity_fragments = list(block.fragments)
        inline_fragments   = self.process_status.inline_fragments()

        if not block.preserve_newlines:
            return clip_fragments(
                [*activity_fragments, *inline_fragments],
                width=max(1, width),
            )

        composed: list[tuple[str, str]] = []
        inserted: bool = False

        for index, (style, text) in enumerate(activity_fragments):
            if not inserted and "\n" in text:
                before, after = text.split("\n", 1)
                if before:
                    composed.append((style, before))
                composed.extend(inline_fragments)
                composed.append(("", "\n"))
                if after:
                    composed.append((style, after))
                composed.extend(activity_fragments[index + 1:])
                inserted = True
                break
            composed.append((style, text))

        if not inserted:
            composed.extend(inline_fragments)

        return join_formatted_lines(
            clip_fragments(line, width=max(1, width))
            for line in split_formatted_lines(composed)
        )

    def _queued_fragments(self, *, width: int | None = None) -> FormattedText:
        """生成动画区域下方的待提交消息。"""
        pending_active = self.pending_steers.active
        queued_active  = self.queued_messages.active
        render_width   = self.terminal_width if width is None else max(1, width)

        pending_rows, queued_rows = queued_row_budget(
            pending_active=pending_active,
            queued_active=queued_active,
            max_height=self.QUEUED_MAX_HEIGHT,
        )

        pending = self.pending_steers.fragments(
            width=render_width,
            max_rows=pending_rows,
        )
        queued = self.queued_messages.fragments(
            width=render_width,
            max_rows=queued_rows,
            edit_binding=self._queued_message_edit_binding,
        )

        return join_queued_fragments(
            pending,
            queued,
            show_leading_gap=self._activity_queue_gap_visible(
                width=render_width,
            ),
        )

    def _footer_fragments(self) -> FormattedText:
        """生成单行 TUI 信息栏。"""
        mode = resolve_footer_mode(
            history_backtrack_primed=(
                self.input_model.history_backtrack_primed
            ),
            exit_armed=self.interrupt_state.exit_armed,
            queue_submission_hint_visible=(
                self._queue_submission_hint_visible()
            ),
            submission_pending=(
                self.document.has_pending_submission
                or self._get_surface_submission_pending()
            ),
        )
        if mode is not FooterMode.DEFAULT:
            return render_footer_fragments(
                mode=mode,
                width=self.terminal_width,
            )

        context = self._get_context()
        return render_footer_fragments(
            mode=mode,
            width=self.terminal_width,
            brand_color=self.input_model.theme()["brand"],
            mailbox_label=(
                f"Mailbox {format_mailbox_count(self.mailbox_overlay.pending_count)}"
                if self.mailbox_overlay.pending_count
                else ""
            ),
            model_label=context.model,
            permissions_label=context.permissions_label,
            workspace_label=context.workspace_label,
        )

    def _transcript_cursor(self) -> Point:
        """让会话内容视口跟随最新输出。"""
        text     = self._transcript_text()
        view_row = self._get_transcript_view_row()

        if view_row is None:
            x, y = cursor_point(text, width=self.terminal_width)
        else:
            continuation_widths, _rows = self._transcript_display_metrics()
            x, y = cursor_point_for_display_row(
                text,
                width=self.terminal_width,
                display_row=view_row,
                continuation_widths=continuation_widths,
            )

        return Point(x=x, y=y)

    def _transcript_key_bindings(self) -> KeyBindings:
        """创建正文视口翻页按键。"""
        bindings     = KeyBindings()
        input_active = has_focus(INPUT_BUFFER_NAME)

        overlay_available = Condition(
            lambda: (
                not self.transcript_overlay.active
                and not self.mailbox_overlay.active
                and not self.static_pager.active
                and not self.resume_picker.active
                and not self._full_screen_overlay_blocked()
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

            request_id = self.transcript_overlay.begin_export(output_format)
            if request_id is None:
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
                        request_id=request_id,
                    )
                    return None

                self.transcript_overlay.set_export_status(
                    f"Exported {result.format}: {result.path}",
                    failed=False,
                    request_id=request_id,
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

    def _mailbox_overlay_key_bindings(self) -> KeyBindings:
        """创建只读消息详情的滚动、翻页和退出按键。"""
        bindings = KeyBindings()
        pager = self.keymap.pager

        @bindings.add("escape", eager=True)
        def _(event) -> None:
            _ = event
            self._close_mailbox_overlay()

        def close(event) -> None:
            _ = event
            self._close_mailbox_overlay()
        self._add_configured_bindings(
            bindings,
            pager.close,
            close,
        )

        def scroll_up(event) -> None:
            _ = event
            self.mailbox_overlay.scroll_lines(-1)
        self._add_configured_bindings(
            bindings,
            pager.scroll_up,
            scroll_up,
        )

        def scroll_down(event) -> None:
            _ = event
            self.mailbox_overlay.scroll_lines(1)
        self._add_configured_bindings(
            bindings,
            pager.scroll_down,
            scroll_down,
        )

        def page_up(event) -> None:
            _ = event
            self.mailbox_overlay.scroll_page(-1)
        self._add_configured_bindings(
            bindings,
            pager.page_up,
            page_up,
        )

        def page_down(event) -> None:
            _ = event
            self.mailbox_overlay.scroll_page(1)
        self._add_configured_bindings(
            bindings,
            pager.page_down,
            page_down,
        )

        def jump_top(event) -> None:
            _ = event
            self.mailbox_overlay.jump_message(to_end=False)
        self._add_configured_bindings(
            bindings,
            pager.jump_top,
            jump_top,
        )

        def jump_bottom(event) -> None:
            _ = event
            self.mailbox_overlay.jump_message(to_end=True)
        self._add_configured_bindings(
            bindings,
            pager.jump_bottom,
            jump_bottom,
        )

        return bindings

    def _static_pager_key_bindings(self) -> KeyBindings:
        """创建静态页面的滚动、翻页和退出按键。"""
        bindings = KeyBindings()
        pager = self.keymap.pager

        def close(event) -> None:
            _ = event
            self._close_static_pager()
        self._add_configured_bindings(bindings, pager.close, close)

        def scroll_up(event) -> None:
            _ = event
            self.static_pager.scroll_lines(-1)
        self._add_configured_bindings(bindings, pager.scroll_up, scroll_up)

        def scroll_down(event) -> None:
            _ = event
            self.static_pager.scroll_lines(1)
        self._add_configured_bindings(bindings, pager.scroll_down, scroll_down)

        def page_up(event) -> None:
            _ = event
            self.static_pager.scroll_page(-1)
        self._add_configured_bindings(bindings, pager.page_up, page_up)

        def page_down(event) -> None:
            _ = event
            self.static_pager.scroll_page(1)
        self._add_configured_bindings(bindings, pager.page_down, page_down)

        def half_page_up(event) -> None:
            _ = event
            self.static_pager.scroll_half_page(-1)
        self._add_configured_bindings(
            bindings,
            pager.half_page_up,
            half_page_up,
        )

        def half_page_down(event) -> None:
            _ = event
            self.static_pager.scroll_half_page(1)
        self._add_configured_bindings(
            bindings,
            pager.half_page_down,
            half_page_down,
        )

        def jump_top(event) -> None:
            _ = event
            self.static_pager.jump(to_end=False)
        self._add_configured_bindings(bindings, pager.jump_top, jump_top)

        def jump_bottom(event) -> None:
            _ = event
            self.static_pager.jump(to_end=True)
        self._add_configured_bindings(bindings, pager.jump_bottom, jump_bottom)

        return bindings

    def _canvas_dimension(self) -> Dimension:
        """返回随内容自然增长并受终端高度限制的画布高度。"""
        return Dimension.exact(self._visible_height())

    def _root_dimension(self) -> Dimension:
        """返回当前主画布或全屏覆盖画布所需高度。"""
        if self.directory_trust.active:
            return self._directory_trust_dimension()
        if self._startup_gate_active:
            return self._startup_dimension()
        if self.transcript_overlay.active:
            return self._transcript_overlay_canvas_dimension()
        if self.mailbox_overlay.active:
            return self._mailbox_overlay_canvas_dimension()
        if self.static_pager.active:
            return self._static_pager_canvas_dimension()
        if self.resume_picker.active:
            return self._resume_picker_dimension()

        return self._canvas_dimension()

    def _resume_picker_dimension(self) -> Dimension:
        """返回 Resume picker 独占的物理终端高度。"""
        return Dimension.exact(self.terminal_height)

    def _directory_trust_dimension(self) -> Dimension:
        """返回启动阶段目录信任界面的显示高度。"""
        return Dimension.exact(self.directory_trust.height())

    def _startup_dimension(self) -> Dimension:
        """返回启动阶段独占菜单画布所需高度。"""
        if not self.bottom_pane.is_active("menu"):
            return Dimension.exact(0)
        return Dimension.exact(self.terminal_height)

    def _startup_menu_dimension(self) -> Dimension:
        """返回启动阶段菜单或空闲输入屏障的显示高度。"""
        return Dimension.exact(self._startup_menu_height())

    def _startup_menu_height(self) -> int:
        """计算启动阶段菜单内容占用的行数。"""
        return self._menu_content_height()

    def _transcript_overlay_height(self) -> int:
        """返回完整记录正文区域可用高度。"""
        return self._transcript_overlay_layout().content_height

    def _transcript_overlay_layout(self) -> OverlayLayout:
        """返回完整记录 overlay 的统一高度预算。"""
        return measure_overlay_layout(
            total_height=self.terminal_height,
            footer_max_height=4,
        )

    def _transcript_overlay_header_height(self) -> int:
        """返回完整记录标题区域高度。"""
        return self._transcript_overlay_layout().header_height

    def _transcript_overlay_footer_height(self) -> int:
        """返回完整记录底栏高度。"""
        return self._transcript_overlay_layout().footer_height

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
        return transcript_header_fragments(
            width=self.terminal_width,
            raw_mode=self.transcript_overlay.raw_mode,
        )

    def _transcript_overlay_separator_fragments(self) -> FormattedText:
        """生成包含滚动百分比的底栏分隔线。"""
        return transcript_separator_fragments(
            width=self.terminal_width,
            percentage=self.transcript_overlay.scroll_percentage(),
        )

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
            marker = "■ " if self.transcript_overlay.export_failed else ""
            return [(style, f" {marker}{self.transcript_overlay.export_status}")]

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

    def _mailbox_overlay_height(self) -> int:
        """返回收件箱正文区域可用高度。"""
        return self._mailbox_overlay_layout().content_height

    def _static_pager_height(self) -> int:
        """返回静态页面正文区域可用高度。"""
        return self._static_pager_layout().content_height

    def _static_pager_layout(self) -> OverlayLayout:
        """返回静态页面的统一高度预算。"""
        return measure_overlay_layout(
            total_height=self.terminal_height,
            footer_max_height=4,
        )

    def _static_pager_header_dimension(self) -> Dimension:
        """返回静态页面标题区域尺寸。"""
        return Dimension.exact(self._static_pager_layout().header_height)

    def _static_pager_footer_dimension(self) -> Dimension:
        """返回静态页面底栏尺寸。"""
        return Dimension.exact(self._static_pager_layout().footer_height)

    def _static_pager_dimension(self) -> Dimension:
        """返回静态页面正文区域尺寸。"""
        return Dimension.exact(self._static_pager_height())

    def _static_pager_canvas_dimension(self) -> Dimension:
        """返回静态页面全屏画布尺寸。"""
        return Dimension.exact(self.terminal_height)

    def _static_pager_header_fragments(self) -> FormattedText:
        """生成静态页面标题。"""
        return static_pager_header_fragments(
            width=self.terminal_width,
            title=self.static_pager.title,
        )

    def _static_pager_separator_fragments(self) -> FormattedText:
        """生成静态页面滚动进度分隔线。"""
        return static_pager_separator_fragments(
            width=self.terminal_width,
            percentage=self.static_pager.scroll_percentage(),
        )

    def _static_pager_primary_help_fragments(self) -> FormattedText:
        """生成静态页面滚动和翻页提示。"""
        pager = self.keymap.pager
        hints = (
            self._paired_key_hint(
                pager.scroll_up,
                pager.scroll_down,
                "to scroll",
            ),
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
        )
        return [("class:static-pager.help", self._help_line(hints))]

    def _static_pager_secondary_help_fragments(self) -> FormattedText:
        """生成静态页面退出提示。"""
        pager = self.keymap.pager
        close = binding_labels(pager.close)
        close_hint = f"{close} to quit" if close else ""
        return [(
            "class:static-pager.help",
            self._help_line((close_hint,)),
        )]

    def _mailbox_overlay_layout(self) -> OverlayLayout:
        """返回收件箱 overlay 的统一高度预算。"""
        return measure_overlay_layout(
            total_height=self.terminal_height,
            footer_max_height=4,
        )

    def _mailbox_overlay_header_height(self) -> int:
        """返回收件箱标题区域高度。"""
        return self._mailbox_overlay_layout().header_height

    def _mailbox_overlay_footer_height(self) -> int:
        """返回收件箱底栏高度。"""
        return self._mailbox_overlay_layout().footer_height

    def _mailbox_overlay_header_dimension(self) -> Dimension:
        """返回收件箱标题区域尺寸。"""
        return Dimension.exact(self._mailbox_overlay_header_height())

    def _mailbox_overlay_footer_dimension(self) -> Dimension:
        """返回收件箱底栏尺寸。"""
        return Dimension.exact(self._mailbox_overlay_footer_height())

    def _mailbox_overlay_dimension(self) -> Dimension:
        """返回收件箱正文区域尺寸。"""
        return Dimension.exact(self._mailbox_overlay_height())

    def _mailbox_overlay_canvas_dimension(self) -> Dimension:
        """返回收件箱全屏画布尺寸。"""
        return Dimension.exact(self.terminal_height)

    def _mailbox_overlay_header_fragments(self) -> FormattedText:
        """生成收件箱标题、数量和监听状态。"""
        return mailbox_header_fragments(
            width=self.terminal_width,
            pending_count_label=format_mailbox_count(
                self.mailbox_overlay.pending_count,
            ),
            listener_active=self.mailbox_overlay.listener_active,
        )

    def _mailbox_overlay_separator_fragments(self) -> FormattedText:
        """生成包含消息正文页码的底栏分隔线。"""
        current, total = self.mailbox_overlay.message_progress()
        return mailbox_separator_fragments(
            width=self.terminal_width,
            current_label=format_mailbox_count(current),
            total_label=format_mailbox_count(total),
            has_multiple=total > 1,
        )

    def _mailbox_overlay_primary_help_fragments(self) -> FormattedText:
        """生成消息正文滚动和翻页提示。"""
        pager = self.keymap.pager
        hints = (
            self._paired_key_hint(
                pager.scroll_up,
                pager.scroll_down,
                "to scroll",
            ),
            self._paired_key_hint(
                pager.page_up,
                pager.page_down,
                "message page",
            ),
            self._paired_key_hint(
                pager.jump_top,
                pager.jump_bottom,
                "to jump",
            ),
        )
        return [("class:mailbox.help", self._help_line(hints))]

    def _mailbox_overlay_secondary_help_fragments(self) -> FormattedText:
        """生成消息只读状态和返回提示。"""
        close = binding_labels(self.keymap.pager.close)
        close_hint = f"Esc/{close} to go back" if close else "Esc to go back"
        return [(
            "class:mailbox.help",
            self._help_line(("Read only", close_hint)),
        )]

    def _transcript_dimension(self) -> Dimension:
        """返回正文当前内容在画布中占用的高度。"""
        _continuation_widths, rows = self._transcript_display_metrics()
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
        height = self._input_height()
        return Dimension(min=1, preferred=height, max=height)

    def _input_surface_dimension(self) -> Dimension:
        """返回包含上下留白的输入表面高度。"""
        return Dimension.exact(self._input_surface_height())

    def _input_stack_dimension(self) -> Dimension:
        """返回输入框、补全列表和当前可见 footer 的总高度。"""
        height = self._input_stack_height()
        return Dimension(min=1, preferred=height, max=height)

    def _approval_dimension(self) -> Dimension:
        """返回审批卡背景区域的当前显示高度。"""
        return Dimension.exact(self._approval_card_height())

    def _approval_footer_dimension(self) -> Dimension:
        """返回审批卡透明提示区域的当前显示高度。"""
        return Dimension.exact(self._approval_footer_height())

    def _menu_dimension(self) -> Dimension:
        """返回内嵌菜单内容当前显示高度。"""
        return Dimension.exact(self._menu_content_height())

    def _menu_top_padding_dimension(self) -> Dimension:
        """返回内嵌菜单顶部对齐留白的显示高度。"""
        return Dimension.exact(self._menu_top_padding_height())

    def _menu_bottom_padding_dimension(self) -> Dimension:
        """返回内嵌菜单底部对齐留白的显示高度。"""
        return Dimension.exact(self._menu_bottom_padding_height())

    def _menu_footer_dimension(self) -> Dimension:
        """返回菜单透明提示区域的当前显示高度。"""
        return Dimension.exact(self._menu_footer_height())

    def _active_view_dimension(self) -> Dimension:
        """返回当前临时交互表面的统一显示高度。"""
        return Dimension.exact(self._active_view_layout().total_height)

    def _bottom_pane_top_inset_dimension(self) -> Dimension:
        """返回正文与整个底部面板之间的外部间距尺寸。"""
        return Dimension.exact(self._bottom_pane_top_inset_height())

    def _status_interaction_gap_dimension(self) -> Dimension:
        """返回状态区与交互区域之间的内部间距尺寸。"""
        return Dimension.exact(self._status_interaction_gap_height())

    def _bottom_pane_content_dimension(self) -> Dimension:
        """返回不含外部顶部间距的底部面板尺寸。"""
        return Dimension.exact(self._bottom_pane_layout().content_height)

    def _status_natural_height(self, *, width: int | None = None) -> int:
        """计算活动状态内容的自然高度。"""
        if self.bottom_pane.transient_active:
            return 0
        text = fragments_text(self._status_fragments())
        if not text:
            return 0
        render_width = self.terminal_width if width is None else max(1, width)
        return min(5, max(1, display_line_count(text, width=render_width)))

    def _status_height(self) -> int:
        """计算动画区域占用行数。"""
        return self._bottom_pane_layout().status_height

    def _queued_natural_height(self, *, width: int | None = None) -> int:
        """计算待提交消息内容的自然高度。"""
        if self._transcript_only or self.bottom_pane.transient_active:
            return 0

        render_width = self.terminal_width if width is None else max(1, width)
        text = fragments_text(self._queued_fragments(width=render_width))
        if not text:
            return 0

        rows = display_line_count(text, width=render_width)
        max_rows = (
            self.QUEUED_MAX_HEIGHT
            + int(self._activity_queue_gap_visible(width=render_width))
        )
        return min(max_rows, max(1, rows))

    def _queued_height(self) -> int:
        """计算待提交消息区域占用行数。"""
        return self._bottom_pane_layout().queued_height

    def _process_status_natural_height(self) -> int:
        """计算后台进程状态内容的自然高度。"""
        if self.bottom_pane.transient_active:
            return 0
        if self.activity_block is not None:
            return 0
        return int(self.process_status.active)

    def _process_status_height(self) -> int:
        """计算后台进程状态区域占用行数。"""
        return self._bottom_pane_layout().process_status_height

    def _footer_height(self) -> int:
        """返回当前输入区 footer 占用高度。"""
        return self._composer_layout().footer_height

    def _footer_visible(self) -> bool:
        """判断输入框下方的信息栏是否应当显示。"""
        return self._footer_height() > 0

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
        return bool(
            self._startup_gate_active
            or self.directory_trust.active
            or self.bottom_pane.transient_active
            or self._completion_visible()
        )

    def _full_screen_overlay_active(self) -> bool:
        """判断当前是否显示由主 Application 管理的全屏覆盖层。"""
        return bool(
            self.transcript_overlay.active
            or self.mailbox_overlay.active
            or self.static_pager.active
            or self.resume_picker.active
        )

    def _full_screen_overlay_blocked(self, *, allow_approval: bool = False) -> bool:
        """判断当前临时表面是否禁止打开全屏覆盖层。"""
        return bool(
            self._startup_gate_active
            or self.directory_trust.active
            or (
                self.bottom_pane.is_active("approval")
                and not allow_approval
            )
            or self.bottom_pane.is_active("menu")
            or self._completion_visible()
        )

    def _completion_visible(self) -> bool:
        """判断输入框是否存在可展示的补全候选项。"""
        if self._get_surface_submission_pending():
            return False

        state = self.input.buffer.complete_state
        return bool(
            self.bottom_pane.input_visible
            and (
                self._completion_fallback_visible()
                or (state is not None and state.completions)
                or self._expected_completion_count()
            )
        )

    def _completion_hint_visible(self) -> bool:
        """判断 skill 补全是否应显示底部提示行。"""
        document = self.input.buffer.document
        query    = skill_query_token(document.text_before_cursor)

        mention_popup = bool(
            query
            and query.startswith("@")
            and self.bottom_pane.input_visible
            and not self._get_surface_submission_pending()
            and self.input_model.completion_menu_completions(document) is not None
        )

        return bool(
            self._native_completion_visible()
            and self.input_model.completion_menu_has_skill_items(document)
            or mention_popup
        )

    def _native_completion_visible(self) -> bool:
        """判断原生补全候选列表是否应当显示。"""
        if self._get_surface_submission_pending():
            return False

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
            and not self._get_surface_submission_pending()
            and self._completion_fallback_fragments()
        )

    def _completion_hint_fragments(self) -> PromptFormattedText:
        """返回 skill 补全使用的底部提示文本。"""
        if not self._completion_hint_visible():
            return PromptFormattedText()
        query = skill_query_token(self.input.buffer.document.text_before_cursor)
        if query and query.startswith("@"):
            return PromptFormattedText(mention_completion_hint_fragments(
                left_padding=TOKEN_MENU_LEFT_PADDING,
                width=self.terminal_width,
                active_mode=self.input_model.skill_search_mode,
            ))
        return PromptFormattedText(completion_hint_fragments(
            left_padding=TOKEN_MENU_LEFT_PADDING,
        ))

    def _completion_fallback_fragments(self) -> PromptFormattedText:
        """返回精确命令或空结果状态使用的展示片段。"""
        document    = self.input.buffer.document
        completions = self.input_model.completion_menu_completions(document)

        if completions is None:
            return PromptFormattedText()

        # 精确 slash 命令由原生 token 菜单渲染，fallback 行仅用于非命令的单项补全。
        if (
            slash_command_query(document) is not None
            and self.input.buffer.complete_state is not None
        ):
            return PromptFormattedText()

        if not completions:
            query = skill_query_token(document.text_before_cursor)
            return PromptFormattedText(completion_empty_fragments(
                left_padding=TOKEN_MENU_LEFT_PADDING,
                mention=bool(query and query.startswith("@")),
                message=self.input_model.completion_empty_message(document),
            ))

        if (
            len(completions) != 1
            or completion_changes_input(document, completions[0])
        ):
            return PromptFormattedText()

        completion = completions[0]

        is_slash_command = (
            slash_command_query(document) is not None
        )

        column_min_width = max(
            self.COMPLETION_COLUMN_MIN_WIDTH,
            TokenCompletionMenuControl.MIN_WIDTH if is_slash_command else 0,
        )

        return PromptFormattedText(completion_candidate_fragments(
            display_text=completion.display_text,
            display_meta_text=completion.display_meta_text,
            is_slash_command=is_slash_command,
            left_padding=TOKEN_MENU_LEFT_PADDING,
            column_min_width=column_min_width,
        ))

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

    def _completion_candidate_count(self) -> int:
        """返回当前已经加载或同步可得的菜单显示行数。"""
        snapshot = self.input_model.token_menu_snapshot(self.input.buffer)
        if snapshot is not None:
            menu_width = self.completion_menu.content.preferred_width(
                self.terminal_width,
            )
            if menu_width is None:
                menu_width = self.terminal_width
            return token_menu_display_height(
                snapshot,
                max(1, min(self.terminal_width, menu_width)),
            )

        state        = self.input.buffer.complete_state
        loaded_count = len(state.completions) if state is not None else 0

        return max(loaded_count, self._expected_completion_count())

    def _composer_layout(self) -> ComposerLayout:
        """返回当前帧统一使用的输入区域高度预算。"""
        return self._bottom_pane_layout().composer

    def _measure_composer_layout(self, *, available_height: int) -> ComposerLayout:
        """在给定底部面板预算内计算输入区域高度。"""
        return measure_composer_layout(
            available_height=available_height,
            input_content_height=self._input_content_height(
                width=self.terminal_width,
            ),
            popup_visible=self._completion_visible(),
            completion_hint_height=self._completion_hint_height(),
            completion_candidate_count=self._completion_candidate_count(),
            input_surface_padding_height=self.INPUT_SURFACE_PADDING_HEIGHT,
            completion_max_height=self.COMPLETION_MAX_HEIGHT,
        )

    def _completion_height(self) -> int:
        """计算无边框补全列表占用行数。"""
        return self._composer_layout().popup_height

    def _completion_hint_height(self) -> int:
        """计算 skill 补全附加提示占用的行数。"""
        return self.COMPLETION_HINT_HEIGHT if self._completion_hint_visible() else 0

    def _completion_section_height(self) -> int:
        """返回补全列表占用高度。"""
        return self._completion_height()

    def _input_stack_height(self) -> int:
        """返回当前完整输入区域占用高度。"""
        return self._composer_layout().input_stack_height

    def _input_surface_height(self) -> int:
        """计算输入内容与上下留白共同占用的高度。"""
        return self._composer_layout().input_surface_height

    def _input_height(self) -> int:
        """计算输入内容占用的显示行数。"""
        return self._composer_layout().input_height

    def _input_auxiliary_height(self, *, width: int | None = None) -> int:
        """返回输入区之外仍需固定展示的辅助区域高度。"""
        status_height         = self._status_natural_height(width=width)
        process_status_height = self._process_status_natural_height()
        queued_height         = self._queued_natural_height(width=width)

        interaction_gap_height = int(
            not queued_height
            and bool(status_height or process_status_height)
        )

        return (
            status_height
            + process_status_height
            + queued_height
            + interaction_gap_height
        )

    def _input_content_height(self, *, width: int) -> int:
        """按指定终端宽度计算输入内容的自然显示行数。"""
        text = self.input.buffer.text
        rows = display_line_count(
            text,
            width=max(1, width - self.INPUT_TEXT_LEFT_MARGIN),
        )

        if text.endswith("\n"):
            rows += 1
        return max(1, rows)

    def _approval_card_height(self) -> int:
        """返回审批内容区域在当前帧中的显示行数。"""
        return (
            self._active_view_layout().content_height
            if self.bottom_pane.is_active("approval")
            else 0
        )

    def _approval_footer_height(self) -> int:
        """返回审批提示区域在当前帧中的显示行数。"""
        return (
            self._active_view_layout().footer_height
            if self.bottom_pane.is_active("approval")
            else 0
        )

    def _menu_content_height(self) -> int:
        """返回菜单内容在当前帧中的显示行数。"""
        self._sync_menu_surface_style()
        return (
            self._active_view_layout().content_height
            if self.bottom_pane.is_active("menu")
            else 0
        )

    def _menu_view_fragments(self) -> StyleAndTextTuples:
        """从底部面板对象栈渲染当前选择 view。"""
        self._sync_menu_surface_style()
        view = self.bottom_pane.active_view
        return view.fragments() if view is not None else []

    def _menu_surface_style(self) -> str:
        """返回当前菜单窗口使用的 surface 样式。"""
        view = self.bottom_pane.active_view
        return view.surface_style() if view is not None else "class:menu-card"

    def _sync_menu_surface_style(self) -> None:
        """按当前 view 同步菜单窗口和上下留白的背景样式。"""
        style = self._menu_surface_style()
        for window in (
            self.menu_window,
            self.menu_top_padding,
            self.menu_bottom_padding,
        ):
            if window.style != style:
                window.style = style

    def _menu_footer_fragments(self) -> StyleAndTextTuples:
        """生成当前菜单表面下方的透明提示片段。"""
        view = self.bottom_pane.active_view
        return view.footer_fragments() if view is not None else []

    def _menu_top_padding_height(self) -> int:
        """返回菜单表面顶部留白在当前帧中的显示行数。"""
        self._sync_menu_surface_style()
        return (
            self._active_view_layout().top_padding_height
            if self.bottom_pane.is_active("menu")
            else 0
        )

    def _menu_bottom_padding_height(self) -> int:
        """返回菜单表面底部留白在当前帧中的显示行数。"""
        self._sync_menu_surface_style()
        return (
            self._active_view_layout().bottom_padding_height
            if self.bottom_pane.is_active("menu")
            else 0
        )

    def _menu_footer_height(self) -> int:
        """返回菜单透明提示区域在当前帧中的显示行数。"""
        return (
            self._active_view_layout().footer_height
            if self.bottom_pane.is_active("menu")
            else 0
        )

    def _bottom_pane_visible(self) -> bool:
        """判断底部状态和交互面板是否参与当前画布。"""
        return not self._transcript_only and not self.directory_trust.active

    def _bottom_pane_outer_top_inset_height(self) -> int:
        """返回整个底部面板固定使用的顶部外部间距。"""
        if not self._bottom_pane_visible():
            return 0
        if self._startup_gate_active:
            return 0
        return min(self.CONTENT_SURFACE_GAP_HEIGHT, self.terminal_height)

    def _bottom_pane_layout(self) -> BottomPaneLayout:
        """返回当前帧底部状态区与交互区域的统一高度结果。"""
        cached = self._bottom_pane_frame_layout
        if self._frame_geometry is not None and cached is not None:
            return cached

        empty_composer = ComposerLayout(
            input_top_padding_height=0,
            input_height=0,
            input_bottom_padding_height=0,
            popup_height=0,
            footer_height=0,
        )
        empty_active_view = ActiveViewLayout(
            surface=None,
            available_height=0,
            top_padding_height=0,
            content_height=0,
            bottom_padding_height=0,
            footer_height=0,
        )

        if not self._bottom_pane_visible():
            layout = BottomPaneLayout(
                outer_top_inset_height=0,
                status_height=0,
                process_status_height=0,
                queued_height=0,
                interaction_gap_height=0,
                composer=empty_composer,
                active_view=empty_active_view,
            )
        else:
            outer_top_inset_height = (
                self._bottom_pane_outer_top_inset_height()
            )
            available_height = max(
                0,
                self.terminal_height - outer_top_inset_height,
            )
            surface = self.bottom_pane.active_surface

            if surface is not None:
                active_view = self._measure_active_view_layout(
                    surface=surface,
                    available_height=available_height,
                )
                layout = BottomPaneLayout(
                    outer_top_inset_height=outer_top_inset_height,
                    status_height=0,
                    process_status_height=0,
                    queued_height=0,
                    interaction_gap_height=0,
                    composer=empty_composer,
                    active_view=active_view,
                )
            else:
                composer = self._measure_composer_layout(
                    available_height=available_height,
                )
                natural_status_height = self._status_natural_height()
                natural_process_status_height = (
                    self._process_status_natural_height()
                )
                natural_queued_height = self._queued_natural_height()
                auxiliary = allocate_auxiliary_pane_layout(
                    available_height=available_height,
                    composer_height=composer.input_stack_height,
                    natural_status_height=natural_status_height,
                    natural_process_status_height=natural_process_status_height,
                    natural_queued_height=natural_queued_height,
                )

                layout = BottomPaneLayout(
                    outer_top_inset_height=outer_top_inset_height,
                    status_height=auxiliary.status_height,
                    process_status_height=auxiliary.process_status_height,
                    queued_height=auxiliary.queued_height,
                    interaction_gap_height=auxiliary.interaction_gap_height,
                    composer=composer,
                    active_view=empty_active_view,
                )

        if self._frame_geometry is not None:
            self._bottom_pane_frame_layout = layout
        return layout

    def _active_view_available_height(self) -> int:
        """返回当前临时交互表面可使用的原始高度预算。"""
        return max(
            1,
            self.terminal_height - self._bottom_pane_outer_top_inset_height(),
        )

    def _active_view_layout(self) -> ActiveViewLayout:
        """返回当前帧临时交互表面的统一高度结果。"""
        return self._bottom_pane_layout().active_view

    def _measure_active_view_layout(
        self,
        *,
        surface: BottomSurface,
        available_height: int
    ) -> ActiveViewLayout:
        """在给定底部面板预算内计算临时交互表面高度。"""
        if surface == "approval":
            card_text   = fragments_text(self.approval.fragments())
            footer_text = fragments_text(self.approval.footer_fragments())
            natural_card_height = (
                display_line_count(card_text, width=self.terminal_width)
                if card_text
                else 0
            )
            natural_footer_height = (
                display_line_count(footer_text, width=self.terminal_width)
                if footer_text
                else 0
            )
            return allocate_approval_view_layout(
                available_height=available_height,
                natural_content_height=natural_card_height,
                natural_footer_height=natural_footer_height,
            )

        if surface == "menu":
            active_view = self.bottom_pane.active_view
            natural_footer_height = (
                active_view.footer_height(self.terminal_width)
                if active_view is not None
                else 0
            )
            natural_content_height = (
                active_view.desired_height(self.terminal_width)
                if active_view is not None
                else 0
            )
            return allocate_menu_view_layout(
                available_height=available_height,
                natural_content_height=natural_content_height,
                natural_footer_height=natural_footer_height,
                vertical_inset=TuiMenu.SURFACE_VERTICAL_INSET,
            )

        raise ValueError(f"Unsupported bottom surface: {surface}")

    def _interaction_height(self) -> int:
        """返回输入区或临时交互表面当前占用的高度。"""
        return self._bottom_pane_layout().interaction_height

    def _visible_height(self) -> int:
        """返回当前内容自然占用的 inline 画布行数。"""
        return self._natural_visible_height()

    def _natural_visible_height(self) -> int:
        """返回不包含稳定高度下限的当前内容自然高度。"""
        height = (
            self._transcript_dimension().preferred
            + self._bottom_pane_layout().total_height
        )

        return max(1, min(self.terminal_height, height))

    def _bottom_pane_top_inset_height(self) -> int:
        """返回正文与整个底部面板之间的外部间距高度。"""
        return self._bottom_pane_layout().outer_top_inset_height

    def _bottom_pane_top_inset_visible(self) -> bool:
        """判断整个底部面板的顶部外部间距是否可见。"""
        return self._bottom_pane_top_inset_height() > 0

    def _status_interaction_gap_height(self) -> int:
        """返回状态区与交互区域之间的内部间距高度。"""
        return self._bottom_pane_layout().interaction_gap_height

    def _status_interaction_gap_visible(self) -> bool:
        """判断状态区与交互区域之间是否保留内部空行。"""
        return self._status_interaction_gap_height() > 0

    def _activity_queue_gap_visible(self, *, width: int | None = None) -> bool:
        """判断活动状态与待提交消息之间是否保留空行。"""
        return bool(
            (
                self._status_natural_height(width=width)
                or self._process_status_natural_height()
            )
            and self._queued_content_visible()
        )

    def _inline_active_transcript_growth_active(self) -> bool:
        """判断动态正文是否应通过终端滚屏为自身扩展画布。"""
        return self.document.active_block is not None

    def _inline_completion_growth_active(self) -> bool:
        """判断补全候选是否允许推动 inline 画布增长。"""
        return bool(
            not self._transcript_only
            and self._completion_visible()
        )

    def _inline_input_growth_active(self, *, width: int) -> bool:
        """判断多行输入是否允许推动 inline 画布增长。"""
        return bool(
            self.bottom_pane.input_visible
            and not self._transcript_only
            and self._input_content_height(width=width) > 1
        )

    def _inline_reply_handoff_growth_active(self) -> bool:
        """判断回复建立正文前是否需要为轮次交接扩展画布。"""
        return bool(
            self._get_submission_deferred()
            and self.bottom_pane.input_visible
            and self.document.active_block is None
            and self.document.visible_tail_kind == "user"
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


if __name__ == '__main__':
    pass
