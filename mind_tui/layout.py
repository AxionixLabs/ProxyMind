# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass
from prompt_toolkit.application import Application
from prompt_toolkit.cursor_shapes import (
    CursorShape,
    SimpleCursorShapeConfig
)
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    HSplit,
    Layout,
    VSplit,
    Window
)
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl
)
from .approval import ApprovalOverlay
from .bottom_pane import BottomPaneRenderer
from .commands import create_command_menu
from .scrollbar import ScrollbarControl
from .state import TuiState
from .style import TUI_STYLE


@dataclass(slots=True)
class TuiLayout:
    """保存应用布局中需要由控制器访问的组件。"""

    application: Application[None]
    input_window: Window
    status_top_spacer: ConditionalContainer
    status_container: ConditionalContainer
    queue_panel: ConditionalContainer
    composer_row: ConditionalContainer
    composer_hint_container: ConditionalContainer
    footer_window: Window


def create_tui_layout(
    *,
    state: TuiState,
    input_control: BufferControl,
    output_window: Window,
    scrollbar_control: ScrollbarControl,
    approval: ApprovalOverlay,
    renderer: BottomPaneRenderer,
    key_bindings: KeyBindings
) -> TuiLayout:
    """组装终端应用的组件树和应用对象。"""
    prompt = Window(
        FormattedTextControl(renderer.prompt_fragments),
        width=2,
        dont_extend_width=True,
        always_hide_cursor=True
    )
    input_window = Window(
        input_control,
        height=1,
        wrap_lines=False,
        dont_extend_height=True,
        style="class:input"
    )
    status_container = ConditionalContainer(
        Window(
            FormattedTextControl(renderer.status_fragments),
            height=1,
            always_hide_cursor=True
        ),
        filter=Condition(renderer.status_visible)
    )
    status_top_spacer = ConditionalContainer(
        Window(height=1),
        filter=Condition(renderer.status_visible)
    )
    queue_panel = ConditionalContainer(
        Window(
            FormattedTextControl(renderer.queue_fragments),
            wrap_lines=True,
            dont_extend_height=True,
            always_hide_cursor=True
        ),
        filter=Condition(lambda: bool(state.turn.queued_messages))
    )
    composer_row = ConditionalContainer(
        VSplit([prompt, input_window]),
        filter=Condition(lambda: state.bottom_pane.composer_visible)
    )
    composer_hint_container = ConditionalContainer(
        Window(
            FormattedTextControl(renderer.composer_hint_fragments),
            height=1,
            always_hide_cursor=True
        ),
        filter=Condition(renderer.composer_hint_visible)
    )
    footer_window = Window(
        FormattedTextControl(renderer.footer_fragments),
        height=1,
        align="CENTER",
        always_hide_cursor=True
    )

    body = HSplit([
        output_window,
        queue_panel,
        status_top_spacer,
        status_container,
        Window(height=1),
        composer_row,
        approval.container,
        Window(height=1),
        composer_hint_container,
        create_command_menu(),
        footer_window
    ])
    surface = VSplit([
        body,
        Window(
            scrollbar_control,
            width=1,
            dont_extend_width=True,
            always_hide_cursor=True
        )
    ])
    application: Application[None] = Application(
        layout=Layout(surface, focused_element=input_control),
        key_bindings=key_bindings,
        style=TUI_STYLE,
        full_screen=False,
        mouse_support=True,
        erase_when_done=False,
        cursor=SimpleCursorShapeConfig(CursorShape.BEAM)
    )
    return TuiLayout(
        application=application,
        input_window=input_window,
        status_top_spacer=status_top_spacer,
        status_container=status_container,
        queue_panel=queue_panel,
        composer_row=composer_row,
        composer_hint_container=composer_hint_container,
        footer_window=footer_window
    )


if __name__ == '__main__':
    pass
