# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from dataclasses import replace

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from frontends.tui.contracts.resume import (
    ResumePickerRequest,
    ResumePickerResult,
    ResumeArchiveStatus,
    ResumePreview,
    ResumePreviewStatus,
    ResumeRow,
    ResumeSessionStatus,
)
from frontends.tui.contracts.text import FormattedText
from ..rendering.menu.resume_picker import (
    ResumePickerState,
    append_resume_query,
    begin_resume_archive,
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
    finish_resume_archive,
    remove_resume_row,
    render_resume_picker,
    resume_picker_list_height,
    selected_resume_row,
    set_resume_preview,
    set_resume_query,
    toggle_resume_density,
    toggle_resume_expansion,
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
        self._invalidate = invalidate
        self._get_width = get_width
        self._get_height = get_height
        self._request_preview = request_preview
        self._request_transcript = request_transcript
        self._cancel_preview = cancel_preview
        self.active: bool = False
        self.state: ResumePickerState | None = None
        self._future: asyncio.Future[ResumePickerResult] | None = None
        self._archive_task: asyncio.Task[None] | None = None
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

        @bindings.add("escape", eager=True)
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
                    if row.status is ResumeSessionStatus.ARCHIVED:
                        if state.request.unarchive_session is not None:
                            self._start_archive_action(row, restoring=True)
                        else:
                            pending = begin_resume_archive(
                                state,
                                row,
                                restoring=True,
                            )
                            self._set_state(finish_resume_archive(
                                pending,
                                row_key=row.key,
                                error="Archived session restore is unavailable",
                            ))
                        return None
                    self.finish(row)

        @bindings.add("c-a")
        def archive(_event) -> None:
            state = self.state
            if state is None or state.transcript_mode:
                return None
            row = selected_resume_row(state)
            if (
                row is not None
                and row.status is ResumeSessionStatus.ACTIVE
                and state.request.archive_session is not None
            ):
                self._start_archive_action(row, restoring=False)

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

        navigation_actions: tuple[
            tuple[str, typing.Literal[
                "up", "down", "home", "end", "page_up", "page_down"
            ]],
            ...,
        ] = (
                ("up", "up"),
                ("down", "down"),
                ("home", "home"),
                ("end", "end"),
                ("pageup", "page_up"),
                ("pagedown", "page_down"),
        )
        for key, action in navigation_actions:
            bindings.add(key)(self._navigation_handler(action))

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
        self._archive_task = None
        self.active = True
        self._invalidate()
        return True

    def close(self) -> None:
        """关闭 picker 并以取消结果解除仍在等待的调用方。"""
        self.active = False
        task = self._archive_task
        self._archive_task = None
        if task is not None and not task.done():
            task.cancel()
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

    def _start_archive_action(self, row: ResumeRow, *, restoring: bool) -> None:
        """启动一次与当前 picker generation 绑定的归档操作。"""
        state = self.state
        if (
            not self.active
            or state is None
            or state.archive_status is not ResumeArchiveStatus.IDLE
        ):
            return None

        callback = (
            state.request.unarchive_session
            if restoring
            else state.request.archive_session
        )
        if callback is None:
            return None

        self._set_state(begin_resume_archive(
            state,
            row,
            restoring=restoring,
        ))
        task = asyncio.create_task(
            self._run_archive_action(callback, row, restoring=restoring),
            name="resume picker archive action",
        )
        self._archive_task = task
        task.add_done_callback(self._archive_done)

    async def _run_archive_action(
        self,
        callback: typing.Callable[..., typing.Awaitable[typing.Any]],
        row: ResumeRow,
        *,
        restoring: bool,
    ) -> None:
        """执行归档回调并把结果投影回当前 picker。"""
        try:
            result = await callback(row)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            state = self.state
            if state is not None:
                self._set_state(finish_resume_archive(
                    state,
                    row_key=row.key,
                    error=str(error).strip() or type(error).__name__,
                ))
            return None

        state = self.state
        if not self.active or state is None:
            return None
        if restoring:
            restored = result if isinstance(result, ResumeRow) else replace(
                row,
                status=ResumeSessionStatus.ACTIVE,
            )
            self._set_state(finish_resume_archive(
                state,
                row_key=row.key,
            ))
            self.finish(restored)
            return None

        self._set_state(remove_resume_row(
            state,
            row_key=row.key,
        ))

    def _archive_done(self, task: asyncio.Task[None]) -> None:
        """回收完成的归档任务并消费未处理异常。"""
        if self._archive_task is task:
            self._archive_task = None
        if not task.cancelled():
            task.exception()


if __name__ == '__main__':
    pass
