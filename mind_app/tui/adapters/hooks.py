# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from functools import partial
from dataclasses import dataclass
from mind_app.presentation.formatting import format_duration_ms
from mind_app.presentation.models import TextStyle
from mind_app.runtime.hooks.models import (
    HookOutputEntry,
    HookRunSummary
)
from ..core.models import (
    FormattedText,
    FragmentBlock
)
from ..rendering.fragments import (
    clip_fragments,
    fragments_text,
    join_formatted_lines,
    transcript_hint,
    wrap_formatted_lines
)
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BODY_STYLE,
    MUTED_STYLE,
    SUCCESS_STYLE,
    prompt_style
)

HOOK_CONTEXT_MAX_DISPLAY_ROWS: typing.Final[int] = 3

_FAILURE_BULLET = TextStyle(foreground="#FF6B6B", bold=True)
_WARNING_BULLET = TextStyle(bold=True)


class TuiHookStatusAdapter:
    """把 Hook 生命周期转换为稳定 TUI 正文块。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self._runtime = runtime

    async def started(self, run: HookRunSummary) -> None:
        """把 Hook 开始事件直接写入稳定正文。"""
        block = _render_start(run)
        self._runtime.append_block(
            block,
            kind="operation",
            raw_text=fragments_text(block.fragments),
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


def _render_start(run: HookRunSummary) -> FragmentBlock:
    """生成一项 Hook 开始记录。"""
    label = f"Running {run.event} hook"
    fragments: FormattedText = [
        (prompt_style(MUTED_STYLE), "•"),
        (prompt_style(BODY_STYLE), f" {label}"),
    ]
    if run.status_message:
        fragments.extend((
            (prompt_style(BODY_STYLE), ": "),
            (prompt_style(MUTED_STYLE), run.status_message),
        ))
    return FragmentBlock(tuple(fragments))


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
    header = f"Ran {run.event} hook"
    if run.status_message:
        header = f"{header}: {run.status_message}"

    result_line: FormattedText = [
        (prompt_style(MUTED_STYLE), f"  └ {run.status}"),
    ]
    if run.duration_ms is not None:
        result_line.append((
            prompt_style(MUTED_STYLE),
            f" · {format_duration_ms(run.duration_ms)}",
        ))

    lines: list[FormattedText] = [[
        (prompt_style(_completion_bullet_style(run)), "•"),
        (prompt_style(BODY_STYLE), f" {header}"),
    ], result_line]

    for entry in run.entries:
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
        (prompt_style(BODY_STYLE), f"    {prefix}{first}"),
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
    rows = [
        row
        if fragments_text(row).startswith("    ")
        else [(prompt_style(BODY_STYLE), "    "), *row]
        for row in rows
    ]
    rows = [
        clip_fragments(row, width=max(1, int(width)))
        for row in rows
    ]
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
