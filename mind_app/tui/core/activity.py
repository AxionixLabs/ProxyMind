# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
import contextlib
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from mind_app.frontend.contracts import ActivityStatusKind
from mind_app.presentation.models import TextStyle
from mind_app.presentation.renderers.upload import (
    upload_idle_block,
    upload_progress_block,
    upload_summary_block
)
from mind_app.presentation.renderers.download import (
    download_progress_block,
    download_summary_block,
)
from mind_core.mcp_status import (
    McpStatusView,
    external_mcp_status_view,
    inbuild_status_view,
)
from .models import FragmentBlock
from .render import clip_fragments
from .styles import (
    BODY_STYLE,
    SUCCESS_STYLE,
    prompt_style,
    styled_block_fragments
)
from .status_frames import (
    StatusFamily,
    render_status_fragments,
    spinner_indicator_fragment,
    status_interval,
    status_phase_rate
)

STATUS_MUTED = TextStyle(foreground="#7F8C9A", dim=True)
STATUS_WARNING = TextStyle(foreground="#FFB86B")
STATUS_FAILURE = TextStyle(foreground="#FF6B6B")
ACTIVITY_SETTLE_SEC: typing.Final[float] = 0.7
ActivitySlotKey = typing.Literal[
    "foreground",
    "attachment",
    "runtime",
    "external_mcp",
]

_SLOT_KEYS: dict[ActivityStatusKind, ActivitySlotKey] = {
    "wait": "foreground",
    "upload": "attachment",
    "download": "runtime",
    "inbuild": "runtime",
    "external_mcp": "external_mcp",
}


@dataclass(slots=True)
class _ActivitySlot(object):
    """保存一项活动动画的渲染和完成状态。"""

    key: ActivitySlotKey
    kind: ActivityStatusKind
    render: typing.Callable[[float], FragmentBlock]
    finalize: typing.Callable[[], FragmentBlock | None] | None = None
    phase: float = 0.0
    expires_at: float | None = None


class TuiActivity(object):
    """生成 TUI 动画专属区域使用的运行期状态帧。"""

    def __init__(
        self,
        *,
        set_renderable: typing.Callable[[FragmentBlock], None],
        clear_renderable: typing.Callable[[], None],
        get_width: typing.Callable[[], int] = lambda: 80,
    ) -> None:
        self.set_renderable   = set_renderable
        self.clear_renderable = clear_renderable
        self.get_width        = get_width

        self.task: asyncio.Task[None] | None = None

        self._wait_elapsed_sec: float       = 0.0
        self._wait_started_at: float | None = None
        self._wait_phase: float             = 0.0
        self._wait_paused: bool             = False

        self._slots: dict[ActivitySlotKey, _ActivitySlot] = {}

    @property
    def active(self) -> bool:
        """返回当前是否存在运行期活动槽位。"""
        return any(slot.expires_at is None for slot in self._slots.values())

    async def begin_wait(self) -> None:
        """启动覆盖当前交互周期的等待动画。"""
        await self._discard("wait")

        self._wait_elapsed_sec = 0.0
        self._wait_phase       = 0.0
        self._wait_paused      = False
        self._wait_started_at  = time.perf_counter()

        await self._set_slot(_ActivitySlot(
            key="foreground",
            kind="wait",
            render=lambda phase: _status_block(
                "thinking",
                family="wait",
                phase=phase,
                elapsed_sec=self._wait_elapsed(),
            ),
        ))

    async def begin_upload(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动附件上传动画。"""
        await self._set_slot(_ActivitySlot(
            key="attachment",
            kind="upload",
            render=lambda phase: _upload_block(snapshot() or {}, phase=phase),
            finalize=lambda: _upload_final_block(snapshot() or {}),
        ))

    async def begin_download(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动运行时下载动画。"""
        await self._set_slot(_ActivitySlot(
            key="runtime",
            kind="download",
            render=lambda phase: _download_block(snapshot() or {}, phase=phase),
            finalize=lambda: _download_final_block(snapshot() or {}),
        ))

    async def begin_inbuild(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动内置运行时状态动画。"""
        await self._set_slot(_ActivitySlot(
            key="runtime",
            kind="inbuild",
            render=lambda phase: _mcp_activity_block(
                inbuild_status_view(snapshot() or {}),
                phase=phase,
                width=self.get_width(),
            ),
            finalize=lambda: _mcp_final_block(
                inbuild_status_view(snapshot() or {}),
                width=self.get_width(),
            ),
        ))

    async def begin_external_mcp(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动外部 MCP 状态动画。"""
        await self._set_slot(_ActivitySlot(
            key="external_mcp",
            kind="external_mcp",
            render=lambda phase: _mcp_activity_block(
                external_mcp_status_view(snapshot() or {}, detail_limit=0),
                phase=phase,
                width=self.get_width(),
            ),
            finalize=lambda: _external_mcp_final_block(
                snapshot() or {},
                width=self.get_width(),
            ),
        ))

    async def stop(
        self,
        kind: ActivityStatusKind | None = None,
    ) -> None:
        """停止指定活动动画，并短暂保留可用的完成状态。"""
        targets = tuple(
            (key, slot)
            for key, slot in self._slots.items()
            if kind is None or slot.kind == kind
        )
        expires_at = asyncio.get_running_loop().time() + ACTIVITY_SETTLE_SEC

        for key, slot in targets:
            if slot.kind == "wait":
                self._reset_wait()

            final = slot.finalize() if slot.finalize is not None else None
            if final is None:
                self._slots.pop(key, None)
                continue

            slot.render = lambda _phase, block=final: block
            slot.expires_at = expires_at

        await self._refresh_task()

    async def clear(self) -> None:
        """停止全部活动动画且不生成最终状态。"""
        self._slots.clear()
        self._reset_wait()
        await self._cancel_task()
        self.clear_renderable()

    async def pause_wait(self) -> bool:
        """暂停当前等待动画和耗时统计。"""
        slot = self._slots.get("foreground")
        if slot is None or slot.kind != "wait":
            return False
        self._slots.pop("foreground", None)

        started_at = self._wait_started_at
        if started_at is not None:
            self._wait_elapsed_sec += max(0.0, time.perf_counter() - started_at)

        self._wait_phase      = slot.phase
        self._wait_started_at = None
        self._wait_paused     = True

        await self._refresh_task()
        return True

    async def resume_wait(self) -> None:
        """从暂停位置恢复等待动画和耗时统计。"""
        if not self._wait_paused or "foreground" in self._slots:
            return None

        self._wait_paused     = False
        self._wait_started_at = time.perf_counter()

        await self._set_slot(_ActivitySlot(
            key="foreground",
            kind="wait",
            phase=self._wait_phase,
            render=lambda phase: _status_block(
                "thinking",
                family="wait",
                phase=phase,
                elapsed_sec=self._wait_elapsed(),
            ),
        ))

    async def _cancel_task(self) -> None:
        """取消活动区域的合成动画任务。"""
        task = self.task
        self.task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _set_slot(self, slot: _ActivitySlot) -> None:
        """添加或替换一项活动动画。"""
        self._slots[slot.key] = slot
        self._render_slots()
        if self.task is None:
            self.task = asyncio.create_task(self._render_loop())

    async def _discard(self, kind: ActivityStatusKind) -> None:
        """移除一项活动动画但不生成最终状态。"""
        key = _SLOT_KEYS[kind]
        slot = self._slots.get(key)
        if slot is not None and slot.kind == kind:
            self._slots.pop(key, None)
        await self._refresh_task()

    async def _refresh_task(self) -> None:
        """根据剩余槽位刷新合成任务和活动区域。"""
        if not self._slots:
            await self._cancel_task()
            self.clear_renderable()
            return None

        self._render_slots()

        if self.task is None:
            self.task = asyncio.create_task(self._render_loop())

    async def _render_loop(self) -> None:
        """持续合成全部活动槽位的动画帧。"""
        interval      = status_interval("wait")
        loop          = asyncio.get_running_loop()
        previous_tick = loop.time()

        try:
            while self._slots:
                await asyncio.sleep(interval)
                current_tick = loop.time()
                step = (
                    max(0.0, current_tick - previous_tick)
                    * status_phase_rate("wait")
                )
                previous_tick = current_tick
                expired = tuple(
                    key
                    for key, slot in self._slots.items()
                    if slot.expires_at is not None and current_tick >= slot.expires_at
                )
                for key in expired:
                    self._slots.pop(key, None)
                for slot in self._slots.values():
                    slot.phase += step
                self._render_slots()
        finally:
            if asyncio.current_task() is self.task:
                self.task = None

    def _render_slots(self) -> None:
        """把全部活动槽位合成为一个多行展示块。"""
        fragments: list[tuple[str, str]] = []
        for slot in self._slots.values():
            block = slot.render(slot.phase)
            if not block.fragments:
                continue
            if fragments:
                fragments.append(("", "\n"))
            fragments.extend(_clip_activity_fragments(
                list(block.fragments),
                width=max(1, int(self.get_width())),
            ))

        if fragments:
            self.set_renderable(FragmentBlock(tuple(fragments)))
        else:
            self.clear_renderable()

    def _wait_elapsed(self) -> float:
        """返回不包含暂停时段的等待耗时。"""
        started_at = self._wait_started_at
        if started_at is None:
            return self._wait_elapsed_sec
        return self._wait_elapsed_sec + max(0.0, time.perf_counter() - started_at)

    def _reset_wait(self) -> None:
        """清空等待动画和耗时统计。"""
        self._wait_elapsed_sec = 0.0
        self._wait_started_at  = None
        self._wait_phase       = 0.0
        self._wait_paused      = False


def _upload_block(data: dict[str, typing.Any], *, phase: float) -> FragmentBlock:
    """生成附件上传活动状态。"""
    event = data.get("event")
    indicator = spinner_indicator_fragment(
        phase,
        family="wait",
    )[1]

    if isinstance(event, dict):
        block = upload_progress_block(event, indicator=indicator)
    else:
        block = upload_idle_block(
            indicator=indicator,
            item_total=int(data.get("item_total") or 0),
            total_bytes=int(data.get("total_bytes") or 0),
        )

    return FragmentBlock(styled_block_fragments(block))


def _upload_final_block(data: dict[str, typing.Any]) -> FragmentBlock | None:
    """生成附件上传完成后的短暂状态。"""
    event = data.get("event")
    if (
        bool(data.get("failed"))
        or not isinstance(event, dict)
        or not bool(event.get("done"))
    ):
        return None
    return FragmentBlock(styled_block_fragments(upload_summary_block(event)))


def _download_block(data: dict[str, typing.Any], *, phase: float) -> FragmentBlock:
    """生成运行时下载活动状态。"""
    indicator = spinner_indicator_fragment(
        phase,
        family="wait",
    )[1]

    block = download_progress_block(data, indicator=indicator)
    return FragmentBlock(styled_block_fragments(block))


def _download_final_block(data: dict[str, typing.Any]) -> FragmentBlock | None:
    """生成运行时下载完成后的短暂状态。"""
    if str(data.get("stage") or "").strip().lower() == "cancelled":
        return None
    block = download_summary_block(data)
    if block is None:
        return None
    return FragmentBlock(styled_block_fragments(block))


def _mcp_activity_block(
    view: McpStatusView,
    *,
    phase: float,
    width: int,
) -> FragmentBlock:
    """生成 MCP 活动区域使用的单行状态。"""
    if view.done:
        return _mcp_final_block(view, width=width) or FragmentBlock(())

    summary = _truncate_display_text(view.summary, limit=max(12, int(width) - 3))
    return _status_block(summary, family="wait", phase=phase, spinner=True)


def _mcp_final_block(view: McpStatusView, *, width: int) -> FragmentBlock | None:
    """生成 MCP 活动区域使用的最终状态。"""
    if not view.done or not view.summary:
        return None

    marker_style = {
        "ready": SUCCESS_STYLE,
        "warning": STATUS_WARNING,
        "failed": STATUS_FAILURE,
    }.get(view.level, BODY_STYLE)

    summary_style = STATUS_FAILURE if view.level == "failed" else BODY_STYLE
    line_limit    = max(12, int(width) - 3)

    fragments: list[tuple[str, str]] = [
        (prompt_style(marker_style), "■"),
        (prompt_style(BODY_STYLE), " "),
        (
            prompt_style(summary_style),
            _truncate_display_text(view.summary, limit=line_limit),
        ),
    ]

    for detail in view.details:
        detail_style = STATUS_MUTED if detail.state == "more" else STATUS_WARNING

        fragments.extend([
            ("", "\n"),
            (
                prompt_style(detail_style),
                _truncate_display_text(detail.text, limit=max(12, int(width))),
            ),
        ])

    return FragmentBlock(tuple(fragments))


def _external_mcp_final_block(
    snapshot: dict[str, typing.Any],
    *,
    width: int,
) -> FragmentBlock | None:
    """生成外部 MCP 活动区域使用的单行最终状态。"""
    return _mcp_final_block(
        external_mcp_status_view(snapshot, detail_limit=0),
        width=width,
    )


def _status_block(
    text: str,
    *,
    family: StatusFamily,
    phase: float,
    started_at: float = 0.0,
    elapsed_sec: float | None = None,
    spinner: bool = False,
) -> FragmentBlock:
    """生成一行 TUI 活动状态。"""
    fragments = render_status_fragments(
        text,
        family=family,
        phase=phase,
        animated=True,
    )
    if spinner:
        fragments[0] = spinner_indicator_fragment(phase, family=family)
    if started_at or elapsed_sec is not None:
        elapsed = (
            max(0.0, float(elapsed_sec))
            if elapsed_sec is not None
            else max(0.0, time.perf_counter() - started_at)
        )
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


def _clip_activity_fragments(
    fragments: list[tuple[str, str]],
    *,
    width: int,
) -> list[tuple[str, str]]:
    """把活动状态裁成单行，并对不完整内容补充省略符。"""
    limit = max(1, int(width))
    single_line = [
        (style, str(text).replace("\n", " "))
        for style, text in fragments
        if text
    ]
    if get_cwidth("".join(text for _, text in single_line)) <= limit:
        return single_line

    clipped = clip_fragments(single_line, width=max(0, limit - 1))
    ellipsis_style = clipped[-1][0] if clipped else ""
    return [*clipped, (ellipsis_style, "…")]


if __name__ == '__main__':
    pass
