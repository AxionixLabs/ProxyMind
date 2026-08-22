# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from ..contracts.resume import (
    ResumePickerRequest,
    ResumePickerResult,
    ResumePreview,
    ResumePreviewStatus,
    ResumeRow
)
from ..contracts.text import FormattedText
from ..rendering.menu.resume_picker import (
    ResumePickerState,
    append_resume_query,
    backspace_resume_query,
    change_resume_toolbar_value,
    create_resume_picker_state,
    delete_resume_query_word,
    ensure_resume_selection_visible,
    enter_resume_transcript,
    exit_resume_transcript,
    focus_resume_toolbar,
    move_resume_transcript,
    move_resume_selection,
    render_resume_picker,
    resume_picker_list_height,
    selected_resume_row,
    set_resume_preview,
    set_resume_query,
    toggle_resume_density,
    toggle_resume_expansion
)


class TuiResumePicker(object):
    """连接 Resume picker 的纯状态、按键和单次等待生命周期。"""

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        get_width: typing.Callable[[], int],
        get_height: typing.Callable[[], int],
        request_preview: typing.Callable[[ResumeRow, int, int], None],
        request_transcript: typing.Callable[[ResumeRow, int, int], None],
        cancel_preview: typing.Callable[[], None],
    ) -> None:
        self._invalidate         = invalidate
        self._get_width          = get_width
        self._get_height         = get_height
        self._request_preview    = request_preview
        self._request_transcript = request_transcript
        self._cancel_preview     = cancel_preview

        self.active: bool = False

        self.state: ResumePickerState | None = None

        self._future: asyncio.Future[ResumePickerResult] | None = None

        self.key_bindings = self._build_key_bindings()

    @property
    def generation(self) -> int:
        """返回当前 picker 会话 generation。"""
        return self.state.generation if self.state is not None else 0

    def _set_state(self, state: ResumePickerState) -> None:
        """替换状态并请求 Screen 重绘。"""
        self.state = state
        self._invalidate()

    def _ensure_visible(self, state: ResumePickerState) -> ResumePickerState:
        """按当前 Screen 尺寸保持选中行可见。"""
        if state.transcript_mode:
            return state
        return ensure_resume_selection_visible(
            state,
            viewport_rows=resume_picker_list_height(self._get_height()),
            width=self._get_width(),
        )

    def _build_key_bindings(self) -> KeyBindings:
        """创建不向主输入传播的 picker modal 按键。"""
        bindings = KeyBindings()

        @bindings.add("c-c")
        def cancel(_event) -> None:
            self.finish(None)

        @bindings.add("escape")
        def escape(_event) -> None:
            state = self.state
            if state is None:
                return None
            if state.transcript_mode:
                self._cancel_preview()
                self._set_state(exit_resume_transcript(state))
                return None
            if state.preview is not None:
                self._set_state(set_resume_preview(
                    toggle_resume_expansion(state),
                    None,
                ))
                return None
            if state.query:
                self._set_state(set_resume_query(state, ""))
                return None
            self.finish(None)

        @bindings.add("enter")
        def accept(_event) -> None:
            state = self.state
            if state is not None and not state.transcript_mode:
                row = selected_resume_row(state)
                if row is not None:
                    self.finish(row)

        @bindings.add(
            "q",
            filter=Condition(
                lambda: bool(
                    self.state is not None and self.state.transcript_mode
                )
            ),
        )
        def quit_transcript(_event) -> None:
            state = self.state
            if state is not None and state.transcript_mode:
                self._cancel_preview()
                self._set_state(exit_resume_transcript(state))

        for key, action in (
            ("up", "up"),
            ("down", "down"),
            ("home", "home"),
            ("end", "end"),
            ("pageup", "page_up"),
            ("pagedown", "page_down"),
        ):
            bindings.add(key)(self._navigation_handler(typing.cast(
                typing.Literal[
                    "up", "down", "home", "end", "page_up", "page_down"
                ],
                action,
            )))

        @bindings.add("tab")
        def focus_next(_event) -> None:
            if self.state is not None and not self.state.transcript_mode:
                self._set_state(focus_resume_toolbar(self.state))

        @bindings.add("s-tab")
        def focus_previous(_event) -> None:
            if self.state is not None and not self.state.transcript_mode:
                self._set_state(focus_resume_toolbar(
                    self.state,
                    reverse=True,
                ))

        @bindings.add("left")
        @bindings.add("right")
        def change_toolbar(_event) -> None:
            if self.state is not None and not self.state.transcript_mode:
                self._set_state(change_resume_toolbar_value(self.state))

        @bindings.add("c-o")
        def toggle_density(_event) -> None:
            state = self.state
            if state is not None and not state.transcript_mode:
                self._set_state(toggle_resume_density(
                    state,
                    viewport_rows=resume_picker_list_height(
                        self._get_height()
                    ),
                    width=self._get_width(),
                ))

        @bindings.add("c-e")
        def toggle_expansion(_event) -> None:
            state = self.state
            if state is not None and not state.transcript_mode:
                self._set_state(self._ensure_visible(
                    toggle_resume_expansion(state)
                ))

        @bindings.add("c-t")
        def load_preview(_event) -> None:
            state = self.state
            if state is None or state.transcript_mode:
                return None
            row = selected_resume_row(state)
            if row is None:
                return None
            if state.request.transcript_loader is not None:
                loading = ResumePreview(
                    row_key=row.key,
                    status=ResumePreviewStatus.LOADING,
                )
                self._set_state(self._ensure_visible(
                    set_resume_preview(
                        enter_resume_transcript(state),
                        loading,
                    )
                ))
                self._request_transcript(
                    row,
                    state.generation,
                    self._get_width(),
                )
                return None
            if state.request.preview_loader is None:
                preview = ResumePreview(
                    row_key=row.key,
                    status=ResumePreviewStatus.ERROR,
                    error="Transcript preview is unavailable",
                )
                self._set_state(self._ensure_visible(
                    set_resume_preview(state, preview)
                ))
                return None
            loading = ResumePreview(
                row_key=row.key,
                status=ResumePreviewStatus.LOADING,
            )
            self._set_state(self._ensure_visible(
                set_resume_preview(state, loading)
            ))
            self._request_preview(row, state.generation, self._get_width())

        @bindings.add("backspace")
        def backspace(_event) -> None:
            if (
                self.state is not None
                and not self.state.transcript_mode
                and self.state.query
            ):
                self._set_state(backspace_resume_query(self.state))

        @bindings.add("c-u")
        def clear_query(_event) -> None:
            if self.state is not None and not self.state.transcript_mode:
                self._set_state(set_resume_query(self.state, ""))

        @bindings.add("c-w")
        def delete_word(_event) -> None:
            if self.state is not None and not self.state.transcript_mode:
                self._set_state(delete_resume_query_word(self.state))

        @bindings.add(Keys.BracketedPaste, eager=True)
        def paste(event) -> None:
            if self.state is not None and not self.state.transcript_mode:
                self._set_state(append_resume_query(self.state, event.data))

        @bindings.add(Keys.Any)
        def insert(event) -> None:
            state = self.state
            value = str(event.data or "")
            if (
                state is not None
                and not state.transcript_mode
                and value
                and value.isprintable()
            ):
                self._set_state(set_resume_query(state, state.query + value))

        return bindings

    def _navigation_handler(
        self,
        action: typing.Literal[
            "up", "down", "home", "end", "page_up", "page_down"
        ],
    ) -> typing.Callable[[typing.Any], None]:
        """为指定列表动作创建同步 prompt_toolkit handler。"""
        def handler(_event) -> None:
            state = self.state
            if state is None:
                return None
            if state.transcript_mode:
                self._set_state(move_resume_transcript(
                    state,
                    action,
                    width=self._get_width(),
                    height=self._get_height(),
                ))
                return None
            self._set_state(move_resume_selection(
                state,
                action,
                viewport_rows=resume_picker_list_height(self._get_height()),
                width=self._get_width(),
            ))

        return handler

    def open(self, request: ResumePickerRequest, *, generation: int) -> bool:
        """打开一个新的 picker 状态并创建独立结果 Future。"""
        if self.active:
            return False
        loop = asyncio.get_running_loop()
        self.state = create_resume_picker_state(
            request,
            generation=generation,
        )
        self._future = loop.create_future()
        self.active = True
        self._invalidate()
        return True

    def close(self) -> None:
        """关闭 picker 并以取消结果解除仍在等待的调用方。"""
        self.active = False
        future = self._future
        if future is not None and not future.done():
            future.set_result(None)
        self._invalidate()

    def finish(self, result: ResumePickerResult) -> None:
        """提交当前 picker 结果且不执行会话恢复。"""
        future = self._future
        if not self.active or future is None or future.done():
            return None
        future.set_result(result)

    def update_preview(
        self,
        preview: ResumePreview,
        *,
        generation: int
    ) -> bool:
        """只接受属于当前 generation 和选中行的 preview 结果。"""
        state = self.state
        if not self.active or state is None or state.generation != generation:
            return False
        updated = set_resume_preview(state, preview)
        if updated is state:
            return False
        self._set_state(self._ensure_visible(updated))
        return True

    def fragments(self) -> FormattedText:
        """按最新终端尺寸渲染完整全屏画布。"""
        state = self.state
        if not self.active or state is None:
            return []
        return render_resume_picker(
            state,
            width=self._get_width(),
            height=self._get_height(),
        )

    async def wait(self) -> ResumePickerResult:
        """等待当前 picker 的选择或取消结果。"""
        future = self._future
        if future is None:
            return None
        return await future


if __name__ == '__main__':
    pass
