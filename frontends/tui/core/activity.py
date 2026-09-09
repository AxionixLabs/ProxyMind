# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import time
import typing
from dataclasses import dataclass

from prompt_toolkit.utils import get_cwidth

from agent.ports import ActivityStatusKind
from agent.ports.presentation import TextStyle
from frontends.terminal.color_support import TerminalColorLevel
from frontends.terminal.mcp_status import (
    McpStatusView,
    external_mcp_status_view,
    inbuild_status_view,
)
from frontends.terminal.renderers.download import (
    download_progress_block,
    download_summary_block,
)
from frontends.terminal.renderers.upload import (
    upload_idle_block,
    upload_progress_block,
    upload_summary_block,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from .models import FragmentBlock
from .status_frames import (
    StatusFamily,
    render_status_fragments,
    spinner_indicator_fragment,
    status_interval,
    status_phase_rate,
)
from .styles import (
    BODY_STYLE,
    SUCCESS_STYLE,
    prompt_style,
    styled_block_fragments,
)
from ..rendering.fragments import clip_fragments

STATUS_MUTED = semantic_text_style(TerminalSemanticRole.SECONDARY)
STATUS_WARNING = semantic_text_style(TerminalSemanticRole.ATTENTION)
STATUS_FAILURE = semantic_text_style(TerminalSemanticRole.FAILURE)

ACTIVITY_SETTLE_SEC: typing.Final[float] = 0.7

ActivitySlotKey = typing.Literal[
    "foreground",
    "attachment",
    "runtime",
    "external_mcp",
    "compact",
    "operation",
]
TurnSurfaceIndicator = typing.Literal[
    "reviewing",
    "thinking",
    "retrying",
    "terminal",
]

_SLOT_KEYS: dict[ActivityStatusKind, ActivitySlotKey] = {
    "wait": "foreground",
    "upload": "attachment",
    "download": "runtime",
    "inbuild": "runtime",
    "external_mcp": "external_mcp",
    "compact": "compact",
    "operation": "operation",
}


def _no_final_block() -> FragmentBlock | None:
    """返回缺省的空最终帧。"""
    return None


def _normalize_status_detail(value: str) -> str:
    """保留状态明细边界并移除不稳定空白。"""
    lines: list[str] = []
    for line in str(value or "").splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


class _ActivitySlot(object):
    """保存一项活动动画的渲染和完成状态。"""

    __slots__ = (
        "key",
        "kind",
        "render",
        "finalize",
        "phase",
        "generation",
        "frozen",
    )

    key: ActivitySlotKey
    kind: ActivityStatusKind
    render: typing.Callable[[float], FragmentBlock]
    finalize: typing.Callable[[], FragmentBlock | None]
    phase: float
    generation: int
    frozen: bool

    def __init__(
        self,
        *,
        key: ActivitySlotKey,
        kind: ActivityStatusKind,
        render: typing.Callable[[float], FragmentBlock],
        finalize: typing.Callable[
            [], FragmentBlock | None
        ] = _no_final_block,
        phase: float = 0.0
    ) -> None:
        self.key = key
        self.kind = kind
        self.render = render
        self.finalize = finalize
        self.phase = phase
        self.generation = 0
        self.frozen = False


@dataclass(frozen=True, slots=True)
class ActivityLease(object):
    """标识一项仍由原启动方持有的活动状态。"""
    key: ActivitySlotKey
    kind: ActivityStatusKind
    generation: int


class TuiActivity(object):
    """生成 TUI 动画专属区域使用的运行期状态帧。"""

    def __init__(
        self,
        *,
        set_renderable: typing.Callable[[FragmentBlock], None],
        clear_renderable: typing.Callable[[], None],
        get_width: typing.Callable[[], int] = lambda: 80,
        color_level: TerminalColorLevel = TerminalColorLevel.UNKNOWN
    ) -> None:
        self.set_renderable = set_renderable
        self.clear_renderable = clear_renderable
        self.get_width = get_width
        self.color_level = color_level
        self.task: asyncio.Task[None] | None = None
        self._settle_task: asyncio.Task[None] | None = None
        self._retired_tasks: set[asyncio.Task[None]] = set()
        self._generation: int = 0
        self._wait_elapsed_sec: float = 0.0
        self._wait_started_at: float | None = None
        self._wait_paused: bool = False
        self._completed_wait_elapsed_sec: float | None = None
        self._wait_phase: float = 0.0
        self._turn_surface_indicator: TurnSurfaceIndicator = "thinking"
        self._turn_surface_title: str = "Thinking"
        self._turn_surface_detail: str = ""
        self._slots: dict[ActivitySlotKey, _ActivitySlot] = {}
        self._settle_deadlines: dict[ActivitySlotKey, float] = {}

    @property
    def active(self) -> bool:
        """返回当前是否存在运行期活动槽位。"""
        return any(key not in self._settle_deadlines for key in self._slots)

    async def begin_wait(self) -> None:
        """启动覆盖当前交互周期的等待动画。"""
        self._reset_wait()
        self._wait_started_at = time.perf_counter()
        await self._activate_wait_slot()

    async def ensure_wait(self) -> None:
        """在模型轮次已接管前台时确保等待动画槽存在。"""
        if self.lease("wait") is not None:
            return None
        self._prepare_wait()
        await self._activate_wait_slot()

    def _prepare_wait(self) -> None:
        """准备计时状态，保持暂停前的累计耗时和动画相位。"""
        if self._wait_paused:
            self._wait_paused = False
            self._wait_started_at = time.perf_counter()
        elif self._wait_started_at is None:
            self._reset_wait()
            self._wait_started_at = time.perf_counter()

    async def show_turn_surface(
        self,
        indicator: TurnSurfaceIndicator,
        *,
        title: str = "",
        detail: str = "",
    ) -> None:
        """在同一前景槽中投影 Turn 的唯一活动提示。"""
        if indicator not in {
            "reviewing",
            "thinking",
            "retrying",
            "terminal",
        }:
            raise ValueError(f"unsupported turn surface indicator: {indicator}")
        self._prepare_wait()
        self._turn_surface_indicator = indicator
        self._turn_surface_title = " ".join(str(title or "").split())
        self._turn_surface_detail = _normalize_status_detail(detail)
        slot = self._slots.get("foreground")
        if slot is None:
            await self._activate_wait_slot()
        elif slot.kind == "wait" and not slot.frozen:
            self._render_slots()

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

    async def begin_compact(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动对话压缩状态动画。"""
        await self._set_slot(_ActivitySlot(
            key="compact",
            kind="compact",
            render=lambda phase: _compact_activity_block(
                snapshot() or {},
                phase=phase,
                width=self.get_width(),
            ),
        ))

    async def begin_operation(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动通用前台操作动画。"""
        await self._set_slot(_ActivitySlot(
            key="operation",
            kind="operation",
            render=lambda phase: _operation_activity_block(
                snapshot() or {},
                phase=phase,
                width=self.get_width(),
            ),
        ))

    async def stop(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True
    ) -> None:
        """停止指定活动动画，并按需短暂保留完成状态。"""
        target_key = _SLOT_KEYS.get(kind) if kind is not None else None

        if kind is None or kind == "wait":
            self._reset_wait()

        targets = tuple(
            (key, slot)
            for key, slot in self._slots.items()
            if kind is None or key == target_key
        )

        expires_at = asyncio.get_running_loop().time() + ACTIVITY_SETTLE_SEC

        for key, slot in targets:
            final = slot.finalize() if settle else None
            if final is None:
                self._slots.pop(key, None)
                self._settle_deadlines.pop(key, None)
                continue

            slot.render = lambda _phase, block=final: block
            slot.frozen = True
            self._settle_deadlines[key] = expires_at

        await self._refresh_task()

    async def clear(self) -> None:
        """停止全部活动动画且不生成最终状态。"""
        self._slots.clear()
        self._settle_deadlines.clear()
        self._cancel_settle_expiry()
        self._reset_wait()
        await self._cancel_task()
        retired = tuple(self._retired_tasks)
        for task in retired:
            task.cancel()
        if retired:
            await asyncio.gather(*retired, return_exceptions=True)
        self.clear_renderable()

    async def hold(self, kind: ActivityStatusKind) -> None:
        """把指定活动槽位保持在最终状态直至后续替换或清除。"""
        key = _SLOT_KEYS[kind]

        slot = self._slots.get(key)
        if slot is None or slot.kind != kind:
            return None

        final = slot.finalize()
        if final is None:
            self._slots.pop(key, None)
        else:
            slot.render = lambda _phase, block=final: block
            slot.frozen = True

        self._settle_deadlines.pop(key, None)
        await self._refresh_task()

    def lease(self, kind: ActivityStatusKind) -> ActivityLease | None:
        """返回指定活动当前可用于视觉交接的租约。"""
        key = _SLOT_KEYS[kind]
        slot = self._slots.get(key)
        if slot is None or slot.kind != kind:
            return None
        return ActivityLease(key, kind, slot.generation)

    def freeze(self, lease: ActivityLease) -> bool:
        """把租约对应的活动冻结为静态最终帧。"""
        slot = self._leased_slot(lease)
        if slot is None:
            return False

        block = slot.finalize() or slot.render(slot.phase)
        slot.render = lambda _phase, frozen=block: frozen
        slot.frozen = True
        self._settle_deadlines.pop(slot.key, None)
        self._render_slots()
        self._schedule_settle_expiry()

        if not self._needs_render_task():
            self._retire_task()
        return True

    def release(self, lease: ActivityLease) -> bool:
        """同步移除租约对应的活动并保留其他槽位。"""
        slot = self._leased_slot(lease)
        if slot is None:
            return False

        if slot.kind == "wait":
            self._complete_wait()
        self._remove_slot(slot)

        return True

    def pause_wait(self) -> bool:
        """暂停并撤下等待槽位，同时保留已经累计的运行耗时。"""
        lease = self.lease("wait")
        slot = self._leased_slot(lease) if lease is not None else None
        if self._wait_started_at is None:
            return False
        self._wait_elapsed_sec += max(
            0.0,
            time.perf_counter() - self._wait_started_at,
        )
        self._wait_started_at = None
        self._wait_paused = True
        if slot is not None:
            self._wait_phase = slot.phase
            self._remove_slot(slot)

        return True

    def hide_wait(self) -> bool:
        """撤下等待槽位并让当前轮次耗时在后台继续累计。"""
        changed = False
        if self._wait_paused:
            self._wait_paused = False
            self._wait_started_at = time.perf_counter()
            changed = True
        lease = self.lease("wait")
        slot = self._leased_slot(lease) if lease is not None else None
        if slot is not None:
            self._wait_phase = slot.phase
            self._remove_slot(slot)
            changed = True
        return changed

    def _remove_slot(self, slot: _ActivitySlot) -> None:
        """撤下指定槽位并同步剩余活动的渲染任务。"""
        self._slots.pop(slot.key, None)
        self._settle_deadlines.pop(slot.key, None)

        if not self._slots:
            self._retire_task()
            self._cancel_settle_expiry()
            self.clear_renderable()
            return None

        self._render_slots()
        self._schedule_settle_expiry()

        if self._needs_render_task():
            self._ensure_task()
        else:
            self._retire_task()

    def finish_wait(self) -> bool:
        """结束当前轮次等待槽位且不影响其他活动。"""
        lease = self.lease("wait")
        if lease is not None:
            return self.release(lease)

        if self._wait_started_at is None and not self._wait_paused:
            return False
        self._complete_wait()
        return True

    def wait_elapsed_seconds(self) -> float | None:
        """返回当前或最近完成轮次不包含暂停时段的累计秒数。"""
        if self._wait_started_at is not None or self._wait_paused:
            return self._wait_elapsed()
        return self._completed_wait_elapsed_sec

    def refresh(self, kind: ActivityStatusKind) -> bool:
        """按最新快照同步刷新指定活动槽位。"""
        key = _SLOT_KEYS[kind]

        slot = self._slots.get(key)
        if slot is None or slot.kind != kind:
            return False

        self._render_slots()

        return True

    def _wait_block(self, phase: float) -> FragmentBlock:
        """按当前连接状态生成等待帧。"""
        if self._turn_surface_indicator in {"terminal", "reviewing"}:
            default_title = (
                "Waiting for background terminal"
                if self._turn_surface_indicator == "terminal"
                else "Reviewing approval request"
            )
            block = _status_block(
                self._turn_surface_title or default_title,
                family="wait",
                phase=phase,
                elapsed_sec=self._wait_elapsed(),
                interrupt_binding="esc",
                sweep=True,
                color_level=self.color_level,
            )
            fragments = list(block.fragments)
            if self._turn_surface_detail:
                for detail_line in self._turn_surface_detail.splitlines():
                    display = _truncate_display_text(
                        detail_line,
                        limit=max(8, int(self.get_width()) - 4),
                    )
                    fragments.extend([
                        ("", "\n"),
                        (
                            prompt_style(STATUS_MUTED),
                            f"  └ {display}",
                        ),
                    ])
            return FragmentBlock(tuple(fragments), preserve_newlines=True)

        retrying = self._turn_surface_indicator == "retrying"
        family: StatusFamily
        if not retrying:
            family = "wait"
        elif self._turn_surface_detail == "transport":
            family = "retry"
        else:
            family = "provider_retry"

        return _status_block(
            self._turn_surface_title or (
                "Retrying" if retrying else "Thinking"
            ),
            family=family,
            phase=phase,
            elapsed_sec=self._wait_elapsed(),
            interrupt_binding="esc",
            sweep=True,
            color_level=self.color_level,
        )

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
        self._generation += 1
        slot.generation = self._generation
        self._slots[slot.key] = slot
        self._settle_deadlines.pop(slot.key, None)
        self._render_slots()
        self._schedule_settle_expiry()
        self._ensure_task()

    async def _discard(self, kind: ActivityStatusKind) -> None:
        """移除一项活动动画但不生成最终状态。"""
        key = _SLOT_KEYS[kind]
        slot = self._slots.get(key)
        if slot is not None and slot.kind == kind:
            self._slots.pop(key, None)
            self._settle_deadlines.pop(key, None)
        await self._refresh_task()

    async def _refresh_task(self) -> None:
        """根据剩余槽位刷新合成任务和活动区域。"""
        if not self._slots:
            self._cancel_settle_expiry()
            await self._cancel_task()
            self.clear_renderable()
            return None

        self._render_slots()
        self._schedule_settle_expiry()

        if self._needs_render_task():
            self._ensure_task()
        else:
            await self._cancel_task()

    async def _render_loop(self) -> None:
        """持续合成全部活动槽位的动画帧。"""
        interval = status_interval("wait")
        loop = asyncio.get_running_loop()
        previous_tick = loop.time()

        try:
            while self._needs_render_task():
                await asyncio.sleep(interval)
                current_tick = loop.time()

                step = (
                    max(0.0, current_tick - previous_tick)
                    * status_phase_rate("wait")
                )

                previous_tick = current_tick

                for slot in self._slots.values():
                    slot.phase += step

                self._render_slots()

        finally:
            if asyncio.current_task() is self.task:
                self.task = None

    def _render_slots(self) -> None:
        """把全部活动槽位合成为一个多行展示块。"""
        fragments: list[tuple[str, str]] = []

        preserve_newlines: bool = False

        for slot in self._slots.values():
            block = slot.render(slot.phase)
            if not block.fragments:
                continue

            preserve_newlines = preserve_newlines or block.preserve_newlines

            clipped = _clip_activity_block(
                list(block.fragments),
                width=max(1, int(self.get_width())),
            )

            if fragments:
                # 合成多个槽位时，使用与稳定正文块一致的空行分隔；通知
                # Screen 保留它，否则单行裁剪会把换行转换为空格。
                preserve_newlines = True
                fragments.append(("", "\n\n"))
            fragments.extend(clipped)

        if fragments:
            self.set_renderable(FragmentBlock(
                tuple(fragments),
                preserve_newlines=preserve_newlines,
            ))
        else:
            self.clear_renderable()

    def _leased_slot(self, lease: ActivityLease) -> _ActivitySlot | None:
        """返回仍与租约匹配的活动槽位。"""
        slot = self._slots.get(lease.key)
        if (
            slot is None
            or slot.kind != lease.kind
            or slot.generation != lease.generation
        ):
            return None
        return slot

    def _needs_render_task(self) -> bool:
        """返回当前槽位是否仍需周期刷新。"""
        return any(not slot.frozen for slot in self._slots.values())

    def _schedule_settle_expiry(self) -> None:
        """按最近截止时间安排冻结状态清理。"""
        self._cancel_settle_expiry()
        if not self._settle_deadlines:
            return None

        self._settle_task = asyncio.create_task(
            self._expire_settled_slots(),
            name="tui activity settlement expiry",
        )

    def _cancel_settle_expiry(self) -> None:
        """取消尚未触发的冻结状态清理。"""
        task = self._settle_task
        self._settle_task = None
        self._retire_owned_task(task)

    async def _expire_settled_slots(self) -> None:
        """移除已经到期的冻结状态并刷新剩余内容。"""
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()

        try:
            deadline = min(self._settle_deadlines.values())
            await asyncio.sleep(max(0.0, deadline - loop.time()))

            if self._settle_task is not task:
                return None

            current_tick = loop.time()

            expired = tuple(
                key
                for key, deadline in self._settle_deadlines.items()
                if current_tick >= deadline
            )

            if expired:
                for key in expired:
                    self._slots.pop(key, None)
                    self._settle_deadlines.pop(key, None)

                if self._slots:
                    self._render_slots()
                else:
                    self.clear_renderable()

            self._settle_task = None
            self._schedule_settle_expiry()
        finally:
            if self._settle_task is task:
                self._settle_task = None

    def _retire_owned_task(self, task: asyncio.Task[None] | None) -> None:
        """同步撤下内部任务并安排回收其结果。"""
        if task is None:
            return None
        if not task.done():
            task.cancel()
        self._retired_tasks.add(task)
        task.add_done_callback(self._retired_task_done)

    def _ensure_task(self) -> None:
        """确保需要动态刷新时存在合成任务。"""
        if self.task is None and self._needs_render_task():
            self.task = asyncio.create_task(self._render_loop())

    def _retire_task(self) -> None:
        """同步取消当前合成任务并在后台回收结果。"""
        task = self.task
        self.task = None
        self._retire_owned_task(task)

    def _retired_task_done(self, task: asyncio.Task[None]) -> None:
        """回收已同步撤下的合成任务。"""
        self._retired_tasks.discard(task)
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    def _wait_elapsed(self) -> float:
        """返回不包含暂停时段的等待耗时。"""
        started_at = self._wait_started_at
        if started_at is None:
            return self._wait_elapsed_sec
        return self._wait_elapsed_sec + max(0.0, time.perf_counter() - started_at)

    def _complete_wait(self) -> None:
        """保存当前轮次累计耗时并清空活动计时状态。"""
        elapsed_sec = self._wait_elapsed()
        self._reset_wait()
        self._completed_wait_elapsed_sec = elapsed_sec

    async def _activate_wait_slot(self) -> None:
        """用当前累计状态恢复前景等待槽位。"""
        await self._set_slot(_ActivitySlot(
            key="foreground",
            kind="wait",
            render=self._wait_block,
            phase=self._wait_phase,
        ))

    def _reset_wait(self) -> None:
        """清空等待动画和耗时统计。"""
        self._wait_elapsed_sec = 0.0
        self._wait_started_at = None
        self._wait_paused = False
        self._completed_wait_elapsed_sec = None
        self._wait_phase = 0.0
        self._turn_surface_indicator = "thinking"
        self._turn_surface_title = "Thinking"
        self._turn_surface_detail = ""

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
    block = download_summary_block(data)
    if block is None:
        return None
    return FragmentBlock(styled_block_fragments(block))


def _compact_activity_block(
    data: dict[str, typing.Any],
    *,
    phase: float,
    width: int
) -> FragmentBlock:
    """生成对话压缩活动区域使用的单行状态。"""
    summary = str(data.get("summary") or "Context compacting...").strip()
    summary = _truncate_display_text(summary, limit=max(12, int(width) - 3))

    return _status_block(
        summary,
        family="wait",
        phase=phase,
        spinner=True,
        sweep=False,
    )


def _operation_activity_block(
    data: dict[str, typing.Any],
    *,
    phase: float,
    width: int
) -> FragmentBlock:
    """生成通用前台操作使用的单行状态。"""
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ValueError("operation activity summary is required")
    summary = _truncate_display_text(summary, limit=max(12, int(width) - 3))

    return _status_block(
        summary,
        family="wait",
        phase=phase,
        spinner=True,
        sweep=False,
    )


def _mcp_activity_block(
    view: McpStatusView,
    *,
    phase: float,
    width: int
) -> FragmentBlock:
    """生成 MCP 活动区域使用的单行状态。"""
    if view.done:
        return _mcp_final_block(view, width=width) or FragmentBlock(())

    summary = _truncate_display_text(view.summary, limit=max(12, int(width) - 3))

    return _status_block(
        summary,
        family="wait",
        phase=phase,
        spinner=True,
        sweep=False,
    )


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
    line_limit = max(12, int(width) - 3)

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
    width: int
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
    interrupt_binding: str | None = None,
    spinner: bool = False,
    sweep: bool = False,
    elapsed_min_sec: float = 0.65,
    color_level: TerminalColorLevel = TerminalColorLevel.UNKNOWN
) -> FragmentBlock:
    """生成一行 TUI 活动状态。"""
    fragments = render_status_fragments(
        text,
        family=family,
        phase=phase,
        animated=sweep,
        color_level=color_level,
    )

    if spinner:
        fragments[0] = spinner_indicator_fragment(
            phase,
            family=family,
            color_level=color_level,
        )

    if started_at or elapsed_sec is not None:
        elapsed = (
            max(0.0, float(elapsed_sec))
            if elapsed_sec is not None
            else max(0.0, time.perf_counter() - started_at)
        )
        if interrupt_binding is not None:
            fragments.extend((
                (
                    prompt_style(STATUS_MUTED),
                    f" ({_compact_elapsed_label(elapsed)} • ",
                ),
                (prompt_style(BODY_STYLE), interrupt_binding),
                (prompt_style(STATUS_MUTED), " to interrupt)"),
            ))
        elif elapsed >= max(0.0, float(elapsed_min_sec)):
            fragments.append((prompt_style(STATUS_MUTED), f" · {_elapsed_label(elapsed)}"))

    return FragmentBlock(tuple(fragments))


def _elapsed_label(elapsed: float) -> str:
    """把经过时间格式化为紧凑标签。"""
    seconds = max(0.0, float(elapsed))
    if seconds < 10:
        # 保持原有四舍五入，同时封顶边界，避免短暂显示 10.0s。
        tenths = min(9.9, round(seconds, 1))
        return f"{tenths:.1f}s"
    if seconds < 60:
        # 与一位小数秒数保持四列宽度，避免 10s 时 footer 左移一列。
        return f"{int(seconds):>3}s"

    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes}m {remaining:02d}s"


def _compact_elapsed_label(elapsed: float) -> str:
    """按 Codex 活动状态格式显示整数秒、分钟和小时。"""
    elapsed_seconds = max(0, int(float(elapsed)))
    if elapsed_seconds < 60:
        return f"{elapsed_seconds}s"
    if elapsed_seconds < 3_600:
        minutes, seconds = divmod(elapsed_seconds, 60)
        return f"{minutes}m {seconds:02d}s"

    hours, remainder = divmod(elapsed_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def _truncate_display_text(text: str, *, limit: int) -> str:
    """按终端显示宽度截断单行状态文本。"""
    value = " ".join(str(text or "").split())
    width_limit = max(1, int(limit))

    if get_cwidth(value) <= width_limit:
        return value

    out: str = ""

    for char in value:
        if get_cwidth(out + char + "…") > width_limit:
            break
        out += char

    return f"{out.rstrip()}…"


def _clip_activity_line(
    fragments: list[tuple[str, str]],
    *,
    width: int
) -> list[tuple[str, str]]:
    """把活动状态裁剪为单行并补充省略符。"""
    limit = max(1, int(width))

    line = [
        (style, str(text).replace("\n", " "))
        for style, text in fragments
        if text
    ]

    if get_cwidth("".join(text for _, text in line)) <= limit:
        return line

    clipped = clip_fragments(line, width=max(0, limit - 1))
    ellipsis_style = clipped[-1][0] if clipped else ""

    return [*clipped, (ellipsis_style, "…")]


def _clip_activity_block(
    fragments: list[tuple[str, str]],
    *,
    width: int,
) -> list[tuple[str, str]]:
    """按行裁剪活动区域，同时保留活动详情的换行。"""
    lines: list[list[tuple[str, str]]] = [[]]
    for style, text in fragments:
        parts = str(text).split("\n")
        for index, part in enumerate(parts):
            if part:
                lines[-1].append((style, part))
            if index < len(parts) - 1:
                lines.append([])

    rendered: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if index:
            rendered.append(("", "\n"))
        rendered.extend(_clip_activity_line(line, width=width))
    return rendered


if __name__ == '__main__':
    pass
