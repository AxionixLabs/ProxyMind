# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from ..contracts.screen import ResumePickerScreenPort
from ..contracts.resume import (
    ResumePickerRequest,
    ResumePickerResult,
    ResumePreview,
    ResumePreviewStatus,
    ResumeRow
)


class ResumePickerViewportPort(typing.Protocol):
    """描述 picker 生命周期使用的原生滚屏协调能力。"""

    def pause_scrollback(self) -> None:
        """暂停当前原生滚屏提交任务。"""
        ...

    def schedule_scrollback_flush(self) -> None:
        """安排 picker 关闭后的原生滚屏恢复。"""
        ...


class ResumePickerCoordinator(object):
    """拥有 picker、preview 任务和原生滚屏恢复的生命周期顺序。"""

    def __init__(
        self,
        *,
        viewport: ResumePickerViewportPort,
        screen: ResumePickerScreenPort,
        cancel_history_backtrack: typing.Callable[[], None],
    ) -> None:
        self._viewport = viewport
        self._screen   = screen

        self._cancel_history_backtrack = cancel_history_backtrack

        self._next_generation: int = 1

        self._active_generation: int | None           = None
        self._request: ResumePickerRequest | None     = None
        self._preview_task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        """返回 coordinator 是否拥有一个已打开的 picker 会话。"""
        return self._active_generation is not None

    async def view(self, request: ResumePickerRequest) -> ResumePickerResult:
        """冻结原生滚屏，打开 picker 并等待选择或取消。"""
        if self.active:
            raise RuntimeError("resume picker is already active")
        self._cancel_history_backtrack()
        self._viewport.pause_scrollback()
        generation = self._next_generation
        self._next_generation += 1
        self._active_generation = generation
        self._request = request

        try:
            opened = self._screen.set_resume_picker(
                True,
                request=request,
                generation=generation,
            )
        except BaseException:
            self._clear_session()
            self._viewport.schedule_scrollback_flush()
            raise
        if not opened:
            self._clear_session()
            self._viewport.schedule_scrollback_flush()
            return None

        try:
            return await self._screen.wait_resume_picker()
        finally:
            await self._cancel_preview()
            try:
                self._screen.set_resume_picker(False)
            finally:
                if self._active_generation == generation:
                    self._clear_session()
                self._viewport.schedule_scrollback_flush()

    def request_preview(
        self,
        row: ResumeRow,
        *,
        generation: int,
        width: int,
    ) -> None:
        """替换当前 preview 任务并绑定 generation 与 row key。"""
        request = self._request
        if generation != self._active_generation or request is None:
            return None
        loader = request.preview_loader
        if loader is None:
            return None
        self._request_load(
            loader,
            row,
            generation=generation,
            width=width,
            task_name="resume transcript preview",
        )

    def request_transcript(
        self,
        row: ResumeRow,
        *,
        generation: int,
        width: int,
    ) -> None:
        """替换当前任务并读取全屏 transcript 内容。"""
        request = self._request
        if generation != self._active_generation or request is None:
            return None
        loader = request.transcript_loader
        if loader is None:
            return None
        self._request_load(
            loader,
            row,
            generation=generation,
            width=width,
            task_name="resume transcript",
        )

    def cancel_preview(self) -> None:
        """取消当前 preview 或全屏 transcript 的后台读取。"""
        task = self._preview_task
        if task is not None and not task.done():
            task.cancel()

    def _request_load(
        self,
        loader: typing.Any,
        row: ResumeRow,
        *,
        generation: int,
        width: int,
        task_name: str,
    ) -> None:
        """启动一次带 generation 校验的 preview/transcript 加载。"""
        task = self._preview_task
        if task is not None and not task.done():
            task.cancel()
        self._preview_task = asyncio.create_task(
            self._load_preview(
                loader,
                row,
                generation=generation,
                width=width,
            ),
            name=task_name,
        )
        self._preview_task.add_done_callback(self._preview_done)

    async def close(self) -> None:
        """取消 preview，解除 picker 等待并恢复原画面。"""
        await self._cancel_preview()
        if self.active:
            try:
                self._screen.set_resume_picker(False)
            finally:
                self._clear_session()
                self._viewport.schedule_scrollback_flush()
            await asyncio.sleep(0)

    async def _load_preview(
        self,
        loader: typing.Any,
        row: ResumeRow,
        *,
        generation: int,
        width: int,
    ) -> None:
        """读取一次 preview 并丢弃迟到或错行结果。"""
        try:
            preview = await loader.load(row, width=max(1, int(width)))
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            preview = ResumePreview(
                row_key=row.key,
                status=ResumePreviewStatus.ERROR,
                error=str(error).strip() or type(error).__name__,
            )
        if (
            generation != self._active_generation
            or preview.row_key != row.key
        ):
            return None
        self._screen.set_resume_preview(preview, generation=generation)

    async def _cancel_preview(self) -> None:
        """取消并等待 coordinator 当前拥有的 preview 任务。"""
        task = self._preview_task
        self._preview_task = None
        if task is None:
            return None
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _preview_done(self, task: asyncio.Task[None]) -> None:
        """回收已完成 preview 任务的当前索引。"""
        if self._preview_task is task:
            self._preview_task = None

    def _clear_session(self) -> None:
        """清除当前 picker request 和 generation 所有权。"""
        self._active_generation = None
        self._request = None


if __name__ == '__main__':
    pass
