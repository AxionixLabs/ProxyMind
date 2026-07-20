# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from rich.text import Text
from mind_core.design import Design
from mind_core.design.upload import UploadProgressLiveReporter


class TuiActivity(object):
    """生成 TUI 动画专属区域使用的运行期状态帧。"""

    def __init__(
        self,
        *,
        set_renderable: typing.Callable[[typing.Any], None],
        clear_renderable: typing.Callable[[], None],
    ) -> None:
        self.set_renderable = set_renderable
        self.clear_renderable = clear_renderable
        self.task: asyncio.Task[None] | None = None

    async def begin_mode(self, mode: str) -> None:
        """启动模式等待动画。"""
        await self._replace(self._mode_loop(mode))

    async def begin_upload(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动附件上传动画。"""
        await self._replace(self._upload_loop(snapshot))

    async def begin_inbuild(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动内置运行时状态动画。"""
        await self._replace(self._snapshot_loop(
            snapshot,
            label="starting runtime",
        ))

    async def begin_external_mcp(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动外部 MCP 状态动画。"""
        await self._replace(self._snapshot_loop(
            snapshot,
            label="starting external MCP",
        ))

    async def stop(self) -> None:
        """停止当前活动动画并清理展示区域。"""
        task = self.task
        self.task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.clear_renderable()

    async def _replace(self, coroutine: typing.Coroutine) -> None:
        """以新的活动动画替换已有动画。"""
        await self.stop()
        self.task = asyncio.create_task(coroutine)

    async def _mode_loop(self, mode: str) -> None:
        """持续生成模式等待动画帧。"""
        family = "mode"
        label = Design.mode_status_text(mode)
        interval = Design.status_interval(family)
        phase = 0.0
        loop = asyncio.get_running_loop()
        started = loop.time()

        while True:
            frame = Design.mode_status_renderable(phase, label)
            frame.append_text(Design.status_elapsed_renderable(loop.time() - started))
            self.set_renderable(frame)
            await asyncio.sleep(interval)
            phase += Design.status_step(family) * interval

    async def _upload_loop(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """持续生成附件上传状态帧。"""
        while True:
            data = snapshot() or {}
            event = data.get("event")
            if isinstance(event, dict):
                frame = UploadProgressLiveReporter.render_progress(event)
            else:
                frame = UploadProgressLiveReporter.render_idle_block(
                    item_total=int(data.get("item_total") or 0),
                    total_bytes=int(data.get("total_bytes") or 0),
                )
            self.set_renderable(frame)
            await asyncio.sleep(1 / 12)

    async def _snapshot_loop(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
        *,
        label: str,
    ) -> None:
        """显示通用运行时快照状态。"""
        while True:
            data = snapshot() or {}
            detail = str(
                data.get("detail")
                or data.get("stage")
                or data.get("phase")
                or ""
            ).strip()
            frame = Text(label, style="bold #AFC7D8")
            if detail:
                frame.append(" · ", style="dim #7F8C9A")
                frame.append(detail, style="dim #7F8C9A")
            self.set_renderable(frame)
            await asyncio.sleep(0.08)


if __name__ == '__main__':
    pass
