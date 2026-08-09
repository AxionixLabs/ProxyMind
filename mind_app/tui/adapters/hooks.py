# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from functools import partial
from mind_app.presentation.models import TextStyle
from mind_app.runtime.hooks.models import (
    HookOutputEntry,
    HookRunSummary
)
from ..core.models import (
    FormattedText,
    FragmentBlock
)
from ..core.render import (
    clip_fragments,
    fragments_text,
    join_formatted_lines,
    wrap_formatted_lines
)
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BODY_STYLE,
    MUTED_STYLE,
    SUCCESS_STYLE,
    prompt_style
)
from .presentation import transcript_hint

HOOK_RUN_REVEAL_DELAY_SEC: typing.Final[float]   = 0.3
QUIET_HOOK_MIN_VISIBLE_SEC: typing.Final[float]  = 0.6
HOOK_CONTEXT_MAX_DISPLAY_ROWS: typing.Final[int] = 3

_FAILURE_BULLET = TextStyle(foreground="#FF6B6B", bold=True)
_WARNING_BULLET = TextStyle(bold=True)

_HookDisplayPhase = typing.Literal[
    "pending",
    "visible",
    "linger",
]


@dataclass(slots=True)
class _HookDisplayRun:
    """保存单次 Hook 调用的可见性状态。"""
    run: HookRunSummary
    phase: _HookDisplayPhase
    reveal_deadline: float
    visible_since: float | None = None
    removal_deadline: float | None = None


class _HookDisplayState:
    """维护 Hook 活动行的延迟显示和安静完成策略。"""

    def __init__(self) -> None:
        self._runs: list[_HookDisplayRun] = []

    def start(self, run: HookRunSummary, *, now: float) -> None:
        """登记调用并从隐藏揭示阶段开始计时。"""
        existing = next(
            (item for item in self._runs if item.run.id == run.id),
            None,
        )
        replacement = _HookDisplayRun(
            run=run,
            phase="pending",
            reveal_deadline=now + HOOK_RUN_REVEAL_DELAY_SEC,
        )
        if existing is None:
            self._runs.append(replacement)
            return None

        self._runs[self._runs.index(existing)] = replacement

    def complete(
        self,
        run: HookRunSummary,
        *,
        now: float
    ) -> HookRunSummary | None:
        """完成调用并返回需要写入正文的结果。"""
        index = next(
            (
                position
                for position, item in enumerate(self._runs)
                if item.run.id == run.id
            ),
            None,
        )
        quiet = run.status == "completed" and not run.entries

        if index is None:
            return None if quiet else run

        current = self._runs[index]
        if not quiet:
            self._runs.pop(index)
            return run

        if current.phase != "visible" or current.visible_since is None:
            self._runs.pop(index)
            return None

        removal_deadline = (
            current.visible_since + QUIET_HOOK_MIN_VISIBLE_SEC
        )
        if now >= removal_deadline:
            self._runs.pop(index)
            return None

        current.run = run
        current.phase = "linger"
        current.removal_deadline = removal_deadline

        return None

    def advance(self, *, now: float) -> bool:
        """推进已经到期的揭示和安静完成状态。"""
        changed: bool = False

        retained: list[_HookDisplayRun] = []

        for item in self._runs:
            if item.phase == "pending" and now >= item.reveal_deadline:
                item.phase = "visible"
                item.visible_since = now
                changed = True
            if (
                item.phase == "linger"
                and item.removal_deadline is not None
                and now >= item.removal_deadline
            ):
                changed = True
                continue
            retained.append(item)

        self._runs = retained
        return changed

    @property
    def visible(self) -> bool:
        """返回是否存在至少一个可见活动行。"""
        return any(item.phase in {"visible", "linger"} for item in self._runs)

    def next_deadline(self) -> float | None:
        """返回下一次可能改变可见状态的时间。"""
        deadlines = [
            deadline
            for item in self._runs
            for deadline in (
                item.reveal_deadline
                if item.phase == "pending"
                else item.removal_deadline
                if item.phase == "linger"
                else None,
            )
            if deadline is not None
        ]
        return min(deadlines) if deadlines else None

    def clear(self) -> None:
        """丢弃全部临时调用状态。"""
        self._runs.clear()

    def snapshot(self) -> dict[str, typing.Any]:
        """返回相邻同类调用合并后的活动行快照。"""
        groups: list[dict[str, typing.Any]] = []

        for item in self._runs:
            if item.phase not in {"visible", "linger"}:
                continue
            key = (item.run.event, item.run.status_message)
            if groups and groups[-1]["key"] == key:
                groups[-1]["count"] += 1
                continue
            groups.append({
                "key": key,
                "event": item.run.event,
                "status_message": item.run.status_message,
                "count": 1,
            })

        return {
            "groups": tuple(
                {
                    key: value
                    for key, value in group.items()
                    if key != "key"
                }
                for group in groups
            ),
        }


class TuiHookStatusAdapter:
    """把 Hook 生命周期转换为 TUI 活动状态和稳定正文。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self._runtime = runtime
        self._state   = _HookDisplayState()
        self._lock    = asyncio.Lock()

        self._timer_task: asyncio.Task[None] | None = None

        runtime.add_turn_finished_callback(self._turn_finished)

    async def started(self, run: HookRunSummary) -> None:
        """登记 Hook 开始事件并安排延迟揭示。"""
        async with self._lock:
            previous_snapshot = self._state.snapshot()
            previous_visible  = self._state.visible

            self._state.start(run, now=self._now())

            if (
                previous_snapshot != self._state.snapshot()
                or previous_visible != self._state.visible
            ):
                await self._apply_surface()

            self._schedule_timer()

    async def completed(self, run: HookRunSummary) -> None:
        """登记 Hook 完成事件并处理临时或持久结果。"""
        async with self._lock:
            previous_snapshot = self._state.snapshot()
            previous_visible  = self._state.visible
            persistent        = self._state.complete(run, now=self._now())

            if (
                persistent is not None
                or previous_snapshot != self._state.snapshot()
                or previous_visible != self._state.visible
            ):
                await self._apply_surface(persistent)

            self._schedule_timer()

    def snapshot(self) -> dict[str, typing.Any]:
        """返回活动区域当前使用的合并状态。"""
        return self._state.snapshot()

    async def _apply_surface(
        self,
        persistent: HookRunSummary | None = None
    ) -> None:
        """把状态变化作为单次画布事务写入 TUI。"""
        visible = self._state.visible

        completion = (
            _completion_blocks(
                persistent,
                self._runtime.terminal_width,
                transcript_key=self._runtime.keymap.open_transcript_label,
            )
            if persistent is not None
            else None
        )

        await self._runtime.transition_hook_status(
            snapshot=self.snapshot,
            visible=visible,
            completed_block=(completion.display if completion else None),
            transcript_block=(
                completion.transcript if completion else None
            ),
            raw_text=(completion.raw_text if completion else None),
            display_renderer=(
                completion.display_renderer if completion else None
            ),
            display_render_width=(
                self._runtime.terminal_width if completion else None
            ),
        )

    def _schedule_timer(self) -> None:
        """仅保留一个指向最近状态截止时间的任务。"""
        current = asyncio.current_task()

        task = self._timer_task
        if task is not None and task is not current and not task.done():
            task.cancel()

        deadline = self._state.next_deadline()
        if deadline is None:
            self._timer_task = None
            return None

        self._timer_task = self._runtime.start_background_task(
            self._wait_for_deadline(deadline),
            name="tui hook status deadline",
        )

    def _turn_finished(self) -> None:
        """在轮次收束时丢弃临时状态和延迟任务。"""
        self._state.clear()
        task = self._timer_task
        self._timer_task = None
        if task is not None and not task.done():
            task.cancel()
        self._runtime.clear_hook_status()

    async def _wait_for_deadline(self, deadline: float) -> None:
        """等待最近截止时间并推进一次显示状态。"""
        await asyncio.sleep(max(0.0, deadline - self._now()))
        task = asyncio.current_task()

        async with self._lock:
            if task is not self._timer_task:
                return None
            self._timer_task = None
            if self._state.advance(now=self._now()):
                await self._apply_surface()
            self._schedule_timer()

    @staticmethod
    def _now() -> float:
        """返回当前事件循环的单调时间。"""
        return asyncio.get_running_loop().time()


@dataclass(frozen=True, slots=True)
class _HookCompletionBlocks:
    """保存完成结果的主画布、完整记录和重排信息。"""
    display: FragmentBlock
    transcript: FragmentBlock
    raw_text: str
    display_renderer: typing.Callable[[int], FragmentBlock]


def _completion_blocks(
    run: HookRunSummary,
    width: int,
    *,
    transcript_key: str
) -> _HookCompletionBlocks:
    """生成完成结果的宽度感知展示块。"""
    display = _render_completion(
        run,
        width,
        full_context=False,
        transcript_key=transcript_key,
    )

    transcript = _render_completion(run, width, full_context=True)

    return _HookCompletionBlocks(
        display=display,
        transcript=transcript,
        raw_text=fragments_text(transcript.fragments),
        display_renderer=partial(
            _render_completion,
            run,
            full_context=False,
            transcript_key=transcript_key,
        ),
    )


def _render_completion(
    run: HookRunSummary,
    width: int,
    *,
    full_context: bool,
    transcript_key: str = ""
) -> FragmentBlock:
    """按当前宽度渲染一项可持久 Hook 完成结果。"""
    lines: list[FormattedText] = []

    warning = next(
        (entry for entry in run.entries if entry.kind == "warning"),
        None,
    )

    warning_lines = warning.text.split("\n") if warning is not None else []
    status        = str(run.status)

    if warning_lines:
        header = f"{run.event} ({status}) says: {warning_lines[0]}"
    else:
        header = f"{run.event} hook ({status})"

    lines.append([
        (prompt_style(_completion_bullet_style(run)), "•"),
        (prompt_style(BODY_STYLE), f" {header}"),
    ])

    for line in warning_lines[1:]:
        lines.append([(prompt_style(BODY_STYLE), f"    {line}" if line else "")])

    for entry in run.entries:
        if entry.kind == "warning":
            continue

        entry_lines = _entry_lines(entry)

        if entry.kind == "context" and not full_context:
            lines.extend(_context_preview(
                entry_lines,
                width=width,
                transcript_key=transcript_key,
            ))
        else:
            lines.extend(entry_lines)

    return FragmentBlock(tuple(join_formatted_lines(lines)))


def _entry_lines(entry: HookOutputEntry) -> list[FormattedText]:
    """把结构化输出条目转换为带缩进的逻辑行。"""
    prefix = {
        "warning": "warning: ",
        "stop": "stop: ",
        "feedback": "feedback: ",
        "context": "hook context: ",
        "error": "error: ",
    }[entry.kind]

    source = entry.text.split("\n")
    first  = source[0] if source else ""

    lines: list[FormattedText] = [[
        (prompt_style(BODY_STYLE), f"  {prefix}{first}"),
    ]]

    lines.extend(
        [(prompt_style(BODY_STYLE), f"    {line}" if line else "")]
        for line in source[1:]
    )

    return lines


def _context_preview(
    lines: list[FormattedText],
    *,
    width: int,
    transcript_key: str
) -> list[FormattedText]:
    """把上下文限制为三行并保留完整记录提示。"""
    rows = wrap_formatted_lines(
        join_formatted_lines(lines),
        width=max(1, int(width)),
    )
    if len(rows) <= HOOK_CONTEXT_MAX_DISPLAY_ROWS:
        return rows

    retained = HOOK_CONTEXT_MAX_DISPLAY_ROWS - 1
    omitted  = len(rows) - retained
    marker   = f"… +{omitted} lines"

    hint_suffix = transcript_hint(
        marker,
        transcript_key,
        width,
        prefix="    ",
    )
    hint = clip_fragments([
        (prompt_style(BODY_STYLE), "    "),
        (
            prompt_style(MUTED_STYLE),
            f"{marker}{hint_suffix}",
        ),
    ], width=max(1, int(width)))

    return [
        *rows[:retained],
        hint,
    ]


def _completion_bullet_style(run: HookRunSummary) -> TextStyle:
    """返回完成状态对应的项目符号样式。"""
    if run.status != "completed":
        return _FAILURE_BULLET
    if any(entry.kind == "warning" for entry in run.entries):
        return _WARNING_BULLET

    return SUCCESS_STYLE


if __name__ == '__main__':
    pass
