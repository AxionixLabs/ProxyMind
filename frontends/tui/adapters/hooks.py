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
from ..core.models import (
    FormattedText,
    FragmentBlock,
)
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BODY_STYLE,
    MUTED_STYLE,
    SUCCESS_STYLE,
    prompt_style
)
from ..rendering.fragments import (
    clip_fragments,
    fragments_text,
    join_formatted_lines,
    split_formatted_lines,
    transcript_hint,
    wrap_formatted_lines
)

HOOK_CONTEXT_MAX_DISPLAY_ROWS: typing.Final[int] = 3

_NEUTRAL_BULLET = TextStyle(foreground="#7F8C9A", bold=True)
_FAILURE_BULLET = TextStyle(foreground="#FF6B6B", bold=True)
_WARNING_BULLET = TextStyle(bold=True)


class TuiHookStatusAdapter:
    """把 Hook 生命周期转换为稳定 TUI 正文块。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self._runtime = runtime

    async def started(self, run: HookRunSummary) -> None:
        """把 Hook 开始事件直接写入稳定正文。"""
        width = self._runtime.terminal_width
        block = _render_start(run, width)
        transcript = _render_start(run, None)
        self._runtime.append_block(
            block,
            kind="operation",
            transcript_block=transcript,
            raw_text=fragments_text(transcript.fragments),
            display_renderer=partial(_render_start, run),
            display_render_width=width,
        )

    async def completed(self, run: HookRunSummary) -> None:
        """把 Hook 完成事件直接写入稳定正文。"""
        completion = _completion_blocks(
            run,
            self._runtime.terminal_width,
            transcript_key=self._runtime.keymap.open_transcript_label,
        )
        self._runtime.append_block(
            completion.display,
            kind="operation",
            transcript_block=completion.transcript,
            raw_text=completion.raw_text,
            display_renderer=completion.display_renderer,
            display_render_width=self._runtime.terminal_width,
        )


def _render_start(
    run: HookRunSummary,
    width: int | None,
) -> FragmentBlock:
    """生成一项 Hook 开始记录。"""
    label = f"Running {run.event} hook"
    content: FormattedText = [
        (prompt_style(BODY_STYLE), label),
    ]
    if run.status_message:
        content.extend((
            (prompt_style(BODY_STYLE), ": "),
            (prompt_style(MUTED_STYLE), run.status_message),
        ))
    lines = _prefixed_rows(
        content,
        first_prefix=[(prompt_style(_NEUTRAL_BULLET), "• ")],
        continuation_prefix=[(prompt_style(MUTED_STYLE), "  ")],
        width=width,
    )
    return FragmentBlock(tuple(join_formatted_lines(lines)))


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

    transcript = _render_completion(run, None, full_context=True)

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
    width: int | None,
    *,
    full_context: bool,
    transcript_key: str = ""
) -> FragmentBlock:
    """按当前宽度渲染一项可持久 Hook 完成结果。"""
    header = f"Ran {run.event} hook"
    if run.status_message:
        header = f"{header}: {run.status_message}"

    result_content: FormattedText = [
        (prompt_style(MUTED_STYLE), run.status),
    ]
    if run.duration_ms is not None:
        result_content.append((
            prompt_style(MUTED_STYLE),
            f" · {format_duration_ms(run.duration_ms)}",
        ))

    lines = _prefixed_rows(
        [(prompt_style(BODY_STYLE), header)],
        first_prefix=[(prompt_style(_completion_bullet_style(run)), "• ")],
        continuation_prefix=[(prompt_style(BODY_STYLE), "  ")],
        width=width,
    )
    lines.extend(_prefixed_rows(
        result_content,
        first_prefix=[(prompt_style(MUTED_STYLE), "  └ ")],
        continuation_prefix=[(prompt_style(MUTED_STYLE), "    ")],
        width=width,
    ))

    for entry in run.entries:
        entry_lines = _entry_lines(entry, width=width)

        if entry.kind == "context" and not full_context:
            lines.extend(_context_preview(
                entry_lines,
                width=width,
                transcript_key=transcript_key,
            ))
        else:
            lines.extend(entry_lines)

    return FragmentBlock(tuple(join_formatted_lines(lines)))


def _entry_lines(
    entry: HookOutputEntry,
    *,
    width: int | None,
) -> list[FormattedText]:
    """把结构化输出条目转换为带缩进的逻辑行。"""
    prefix = {
        "warning": "warning: ",
        "stop": "stop: ",
        "feedback": "feedback: ",
        "context": "hook context: ",
        "error": "error: ",
    }[entry.kind]

    source = str(entry.text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while source and not source[0].strip():
        source.pop(0)
    while source and not source[-1].strip():
        source.pop()
    if not source:
        return []

    lines: list[FormattedText] = []
    for index, line in enumerate(source):
        content = f"{prefix}{line}" if index == 0 else line
        lines.extend(_prefixed_rows(
            [(prompt_style(BODY_STYLE), content)],
            first_prefix=[(prompt_style(BODY_STYLE), "    ")],
            continuation_prefix=[(prompt_style(BODY_STYLE), "    ")],
            width=width,
        ))

    return lines


def _context_preview(
    lines: list[FormattedText],
    *,
    width: int | None,
    transcript_key: str
) -> list[FormattedText]:
    """把上下文限制为三行并保留完整记录提示。"""
    if not isinstance(width, int) or width <= 0:
        return lines

    rows = lines
    rows = [
        clip_fragments(row, width=max(1, int(width)))
        for row in rows
    ]
    if len(rows) <= HOOK_CONTEXT_MAX_DISPLAY_ROWS:
        return rows

    retained = HOOK_CONTEXT_MAX_DISPLAY_ROWS - 1
    omitted = len(rows) - retained
    marker = f"… +{omitted} lines"

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
