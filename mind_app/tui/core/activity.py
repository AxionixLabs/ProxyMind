# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
import contextlib
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.models import TextStyle
from mind_app.presentation.renderers.upload import (
    upload_idle_block,
    upload_progress_block
)
from .models import FragmentBlock
from .styles import (
    prompt_style,
    styled_block_fragments
)
from .status_frames import (
    StatusFamily,
    render_status_fragments,
    status_indicator_fragment,
    status_interval,
    status_phase_rate
)

STATUS_MUTED   = TextStyle(foreground="#7F8C9A", dim=True)


class TuiStatusState(object):
    """管理 TUI 单轮状态内容和动画相位。"""

    def __init__(self) -> None:
        self.text: str            = ""
        self.family: StatusFamily = "tool"
        self.phase: float         = 0.0
        self.animated: bool       = True
        self.started_at: float    = 0.0

    @property
    def visible(self) -> bool:
        """返回当前状态是否可见。"""
        return bool(self.text)

    @property
    def animating(self) -> bool:
        """返回当前状态是否需要持续刷新。"""
        return self.visible and self.animated

    def set_status(
        self,
        text: str | None,
        *,
        family: StatusFamily = "tool",
        animated: bool = True,
    ) -> None:
        """更新状态文本并重置动画起点。"""
        self.text       = _truncate_display_text(str(text or "").strip(), limit=48)
        self.family     = family
        self.animated   = bool(animated)
        self.phase      = 0.0
        self.started_at = time.perf_counter() if self.text else 0.0

    def set_phase(self, phase: float) -> None:
        """更新活动状态的动画相位。"""
        if self.visible:
            self.phase = float(phase or 0.0)

    def reset(self) -> None:
        """清空状态内容和动画数据。"""
        self.text       = ""
        self.family     = "tool"
        self.phase      = 0.0
        self.animated   = True
        self.started_at = 0.0

    def interval(self) -> float:
        """返回 TUI 状态刷新间隔。"""
        return status_interval(self.family)

    def phase_rate(self) -> float:
        """返回 TUI 状态动画相位速率。"""
        return status_phase_rate(self.family)

    def render_block(self) -> FragmentBlock:
        """生成当前状态行的 prompt_toolkit 片段。"""
        if not self.visible:
            return FragmentBlock(())

        fragments = render_status_fragments(
            self.text,
            family=self.family,
            phase=self.phase,
            animated=self.animated,
        )

        elapsed = max(0.0, time.perf_counter() - self.started_at)

        if elapsed >= 0.65:
            fragments.append((prompt_style(STATUS_MUTED), f" · {_elapsed_label(elapsed)}"))

        return FragmentBlock(tuple(fragments))


class TuiActivity(object):
    """生成 TUI 动画专属区域使用的运行期状态帧。"""

    def __init__(
        self,
        *,
        set_renderable: typing.Callable[[FragmentBlock], None],
        clear_renderable: typing.Callable[[], None],
    ) -> None:
        self.set_renderable = set_renderable
        self.clear_renderable = clear_renderable
        self.task: asyncio.Task[None] | None = None

    async def begin_wait(self) -> None:
        """启动覆盖当前交互周期的等待动画。"""
        await self._replace(self._wait_loop())

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
        await self._replace(self._snapshot_loop(snapshot, label="starting runtime"))

    async def begin_external_mcp(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动外部 MCP 状态动画。"""
        await self._replace(self._snapshot_loop(snapshot, label="starting external MCP"))

    async def stop(self) -> None:
        """停止当前活动动画并清理展示区域。"""
        task = self.task
        self.task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.clear_renderable()

    async def _replace(self, coroutine: typing.Coroutine[typing.Any, typing.Any, None]) -> None:
        """以新的活动动画替换已有动画。"""
        await self.stop()
        self.task = asyncio.create_task(coroutine)

    async def _wait_loop(self) -> None:
        """持续生成覆盖当前交互周期的等待动画帧。"""
        started  = time.perf_counter()
        phase    = 0.0
        interval = status_interval("wait")

        while True:
            self.set_renderable(_status_block(
                "thinking",
                family="wait",
                phase=phase,
                started_at=started,
            ))
            await asyncio.sleep(interval)
            phase += status_phase_rate("wait") * interval

    async def _upload_loop(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """持续生成附件上传状态帧。"""
        phase    = 0.0
        interval = 1 / 12

        while True:
            data      = snapshot() or {}
            event     = data.get("event")
            indicator = status_indicator_fragment(
                phase,
                family="wait",
                animated=True,
            )[1]

            if isinstance(event, dict):
                block = upload_progress_block(event, indicator=indicator)
            else:
                block = upload_idle_block(
                    indicator=indicator,
                    item_total=int(data.get("item_total") or 0),
                    total_bytes=int(data.get("total_bytes") or 0),
                )
            self.set_renderable(FragmentBlock(styled_block_fragments(block)))
            await asyncio.sleep(interval)
            phase += status_phase_rate("wait") * interval

    async def _snapshot_loop(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
        *,
        label: str,
    ) -> None:
        """显示通用运行时快照状态。"""
        phase    = 0.0
        interval = status_interval("wait")

        while True:
            data    = snapshot() or {}
            summary = str(data.get("summary") or label).strip() or label

            detail = str(
                data.get("detail") or data.get("stage") or data.get("phase") or ""
            ).strip()

            text = f"{summary} · {detail}" if detail else summary

            self.set_renderable(_status_block(text, family="wait", phase=phase))

            await asyncio.sleep(interval)
            phase += status_phase_rate("wait") * interval


def _status_block(
    text: str,
    *,
    family: StatusFamily,
    phase: float,
    started_at: float = 0.0,
) -> FragmentBlock:
    """生成一行 TUI 活动状态。"""
    fragments = render_status_fragments(
        text,
        family=family,
        phase=phase,
        animated=True,
    )
    if started_at:
        elapsed = max(0.0, time.perf_counter() - started_at)
        if elapsed >= 0.65:
            fragments.append((prompt_style(STATUS_MUTED), f" · {_elapsed_label(elapsed)}"))

    return FragmentBlock(tuple(fragments))


def _elapsed_label(elapsed: float) -> str:
    """把经过时间格式化为紧凑标签。"""
    seconds = max(0.0, float(elapsed))
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes}m {remaining:02d}s"


def _truncate_display_text(text: str, *, limit: int) -> str:
    """按终端显示宽度截断单行状态文本。"""
    value       = " ".join(str(text or "").split())
    width_limit = max(1, int(limit))

    if get_cwidth(value) <= width_limit:
        return value

    out = ""

    for char in value:
        if get_cwidth(out + char + "…") > width_limit:
            break
        out += char

    return f"{out.rstrip()}…"


if __name__ == '__main__':
    pass
