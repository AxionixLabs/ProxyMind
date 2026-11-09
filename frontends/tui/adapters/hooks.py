# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from functools import partial

from prompt_toolkit.utils import get_cwidth

from agent.application.hooks.models import (
    HookOutputEntry,
    HookRunSummary,
)
from agent.ports.presentation import TextStyle
from frontends.terminal.formatting import format_duration_ms
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from ..core.models import (
    FormattedText,
    FragmentBlock,
)
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BODY_STYLE,
    MUTED_STYLE,
    SUCCESS_STYLE,
    prompt_style,
)
from ..rendering.fragments import (
    fragments_text,
    join_formatted_lines,
    split_formatted_lines,
    wrap_formatted_lines,
)

_FAILURE_BULLET = semantic_text_style(TerminalSemanticRole.FAILURE, bold=True)
_WARNING_BULLET = TextStyle(bold=True)


class TuiHookStatusAdapter:
    """投影临时 Hook 活动，并只持久展示有输出或异常的完成结果。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self._runtime = runtime
        self._turn_generation = 0
        self._run_generations: dict[str, int] = {}
        runtime.add_turn_finished_callback(self._finish_turn)

    def _finish_turn(self) -> None:
        """隔离旧轮异步 Hook 的迟到完成事件。"""
        self._turn_generation += 1

    async def started(self, run: HookRunSummary) -> None:
        """在活动区域延迟显示 Hook，避免快速执行闪烁或污染正文。"""
        turn_scoped = run.scope == "turn" or self._runtime.execution_active
        if turn_scoped:
            self._run_generations[run.id] = self._turn_generation
            if self._runtime.turn_output_suppressed:
                return
        self._runtime.activity.start_hook(
            run.id,
            _run_label(run),
            started_at=run.started_at,
            turn_scoped=turn_scoped,
            background=run.execution_mode == "async",
        )

    async def completed(self, run: HookRunSummary) -> None:
        """收束临时活动，过滤旧轮输出并保存需要用户关注的结果。"""
        with self._runtime.screen.visual_update():
            self._runtime.activity.finish_hook(run.id)
            generation = self._run_generations.pop(run.id, None)
            if (run.scope == "turn" or generation is not None) and (
                (generation is not None and generation != self._turn_generation)
                or (self._runtime.turn_output_suppressed and run.event != "Interrupt")
            ):
                return
            if run.status == "completed" and all(entry.kind == "context" for entry in run.entries):
                return
            completion = _completion_blocks(run, self._runtime.terminal_width)
            self._runtime.append_block(
                completion.display,
                kind="operation",
                transcript_block=completion.transcript,
                raw_text=completion.raw_text,
                display_renderer=completion.display_renderer,
                display_render_width=self._runtime.terminal_width,
            )


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
) -> _HookCompletionBlocks:
    """生成完成结果的宽度感知展示块。"""
    display = _render_completion(run, width)
    transcript = _render_completion(run, None)

    return _HookCompletionBlocks(
        display=display,
        transcript=transcript,
        raw_text=fragments_text(transcript.fragments),
        display_renderer=partial(_render_completion, run),
    )


def _run_label(run: HookRunSummary) -> str:
    """为运行与结果保持同一条单行标题。"""
    return " ".join(run.status_message.split()) or f"{run.event} hook"


def _render_completion(
    run: HookRunSummary,
    width: int | None,
) -> FragmentBlock:
    """按当前宽度渲染一项可持久 Hook 完成结果。"""
    header: FormattedText = [
        (prompt_style(BODY_STYLE), _run_label(run)),
        (prompt_style(MUTED_STYLE), f" · {run.status}"),
    ]
    if run.duration_ms is not None:
        header.append((
            prompt_style(MUTED_STYLE),
            f" · {format_duration_ms(run.duration_ms)}",
        ))

    lines = _prefixed_rows(
        header,
        first_prefix=[(prompt_style(_completion_bullet_style(run)), "• ")],
        continuation_prefix=[(prompt_style(BODY_STYLE), "  ")],
        width=width,
    )
    for entry in run.entries:
        lines.extend(_entry_lines(entry, width=width))

    return FragmentBlock(tuple(join_formatted_lines(lines)))


def _entry_lines(
    entry: HookOutputEntry,
    *,
    width: int | None,
) -> list[FormattedText]:
    """过滤模型上下文，把面向用户的条目转换为带缩进的逻辑行。"""
    if entry.kind == "context":
        return []

    source = entry.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while source and not source[0].strip():
        source.pop(0)
    while source and not source[-1].strip():
        source.pop()
    if not source:
        return []

    lines: list[FormattedText] = []
    for index, line in enumerate(source):
        content = f"{entry.kind}: {line}" if index == 0 else line
        lines.extend(_prefixed_rows(
            [(prompt_style(BODY_STYLE), content)],
            first_prefix=[(prompt_style(BODY_STYLE), "  └ " if index == 0 else "    ")],
            continuation_prefix=[(prompt_style(BODY_STYLE), "    ")],
            width=width,
        ))

    return lines


def _prefixed_rows(
    content: FormattedText,
    *,
    first_prefix: FormattedText,
    continuation_prefix: FormattedText,
    width: int | None,
) -> list[FormattedText]:
    """按指定前缀生成宽度感知的格式化物理行。"""
    if not isinstance(width, int) or width <= 0:
        logical_rows = split_formatted_lines(content)
        return [
            [*(first_prefix if index == 0 else continuation_prefix), *row]
            for index, row in enumerate(logical_rows)
        ]

    prefix_width = max(
        get_cwidth(fragments_text(first_prefix)),
        get_cwidth(fragments_text(continuation_prefix)),
    )
    rows = wrap_formatted_lines(
        content,
        width=max(1, width - prefix_width),
    )
    return [
        [*(first_prefix if index == 0 else continuation_prefix), *row]
        for index, row in enumerate(rows)
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
