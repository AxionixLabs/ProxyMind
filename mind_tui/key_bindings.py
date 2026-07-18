# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Callable
from dataclasses import dataclass
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import (
    Condition,
    has_focus
)
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import BufferControl
from .input_events import InputEvent
from .state import TuiState


@dataclass(frozen=True, slots=True)
class InputBindingActions:
    """定义终端按键可触发的应用操作。"""

    dispatch: Callable[[InputEvent], None]
    scroll_output: Callable[[int], None]
    copy_selection: Callable[[str], None]


def create_key_bindings(
    *,
    state: TuiState,
    input_buffer: Buffer,
    output_buffer: Buffer,
    input_control: BufferControl,
    output_window: Window,
    actions: InputBindingActions
) -> KeyBindings:
    """创建把终端按键转换为输入语义事件的绑定。"""
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event) -> None:
        if event.app.current_buffer is input_buffer:
            completion_state = input_buffer.complete_state
            if (
                completion_state is not None
                and completion_state.current_completion is not None
            ):
                input_buffer.apply_completion(completion_state.current_completion)
                return
            input_buffer.validate_and_handle()

    @bindings.add("tab")
    def _complete(event) -> None:
        if event.app.current_buffer is not input_buffer:
            return
        completion_state = input_buffer.complete_state
        if completion_state is not None and completion_state.completions:
            completion = (
                completion_state.current_completion
                or completion_state.completions[0]
            )
            input_buffer.apply_completion(completion)
            return
        if state.turn.busy and input_buffer.text.strip():
            actions.dispatch(InputEvent("queue"))
            return
        input_buffer.start_completion(select_first=False)

    @bindings.add("c-o")
    def _newline(event) -> None:
        if event.app.current_buffer is input_buffer:
            input_buffer.insert_text("\n")

    @bindings.add("escape", eager=True)
    def _interrupt_and_send(event) -> None:
        if event.app.current_buffer is not input_buffer:
            return
        if input_buffer.complete_state is not None:
            input_buffer.cancel_completion()
            return
        if state.turn.busy and state.turn.queued_messages:
            actions.dispatch(InputEvent("interrupt"))

    queue_rollback = has_focus(input_buffer) & Condition(
        lambda: not input_buffer.text and bool(state.turn.queued_messages)
    )

    @bindings.add(Keys.ControlLeft, filter=queue_rollback, eager=True)
    def _rollback_queue(event) -> None:
        actions.dispatch(InputEvent("rollback"))

    @bindings.add("backspace", eager=True)
    def _backspace(event) -> None:
        if event.app.current_buffer is not input_buffer:
            return
        if not input_buffer.text and state.composer.shell_mode:
            state.composer.shell_mode = False
            event.app.invalidate()
            return
        input_buffer.delete_before_cursor(count=max(1, event.arg))

    @bindings.add("c-u", eager=True)
    def _clear_input(event) -> None:
        if event.app.current_buffer is input_buffer:
            input_buffer.reset()
            state.composer.shell_mode = False
            event.app.invalidate()

    @bindings.add("c-c")
    def _copy_or_cancel(event) -> None:
        if event.app.current_buffer is output_buffer and output_buffer.selection_state:
            data = output_buffer.copy_selection()
            actions.copy_selection(data.text)
            event.app.layout.focus(input_control)
            return
        if input_buffer.text:
            input_buffer.reset()
            state.composer.shell_mode = False
            return
        event.app.exit(result=None)

    @bindings.add("pageup")
    def _page_up(event) -> None:
        info = output_window.render_info
        actions.scroll_output(-max(1, (info.window_height - 1) if info else 1))

    @bindings.add("pagedown")
    def _page_down(event) -> None:
        info = output_window.render_info
        actions.scroll_output(max(1, (info.window_height - 1) if info else 1))

    return bindings


if __name__ == '__main__':
    pass
