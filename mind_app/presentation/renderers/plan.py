# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.views import (
    PlanStepsStartView,
    PlanUpdateView,
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from ..styles import (
    COMMAND_HEAD_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)
from ..terminal_text import sanitize_terminal_text
from ..text_layout import (
    layout_styled_line,
    text_display_width,
)

PLAN_SUMMARY_STYLE = TextStyle(
    foreground=PREVIEW_MORE_STYLE.foreground,
    bold=True,
    dim=True,
    italic=True,
)

PLAN_ACTIVE_BODY_STYLE   = TextStyle(foreground="#5EEAD4", bold=True)
PLAN_INACTIVE_BODY_STYLE = PREVIEW_TEXT_STYLE


def render_plan_update_view(
    view: PlanUpdateView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把计划更新视图转换为中立展示块。"""
    spans = [
        TextSpan("•", SUCCESS_DOT_STYLE),
        TextSpan(" Updated Plan", TITLE_STYLE),
    ]

    explanation_lines = _content_lines(view.explanation)
    for line_index, line in enumerate(explanation_lines):
        _append_layout_line(
            spans,
            [TextSpan(line, PLAN_SUMMARY_STYLE)],
            first_prefix=TextSpan(
                "  └ " if line_index == 0 else "    ",
                PLAN_SUMMARY_STYLE,
            ),
            continuation_prefix=TextSpan("    ", PLAN_SUMMARY_STYLE),
            terminal_width=terminal_width,
            measure_width=measure_width,
        )

    for index, item in enumerate(view.items):
        icon   = "✔" if item.status == "completed" else "□"
        style  = PLAN_ACTIVE_BODY_STYLE if item.status == "in_progress" else PLAN_INACTIVE_BODY_STYLE
        indent = "    " if explanation_lines or index else "  └ "
        prefix = f"{indent}{icon} "
        continuation = " " * _width_of(prefix, measure_width)

        for line_index, line in enumerate(_content_lines(item.step)):
            _append_layout_line(
                spans,
                [TextSpan(line, style)],
                first_prefix=TextSpan(
                    prefix if line_index == 0 else continuation,
                    TextStyle(),
                ),
                continuation_prefix=TextSpan(continuation),
                terminal_width=terminal_width,
                measure_width=measure_width,
            )

    return StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
        preserve_spans=True,
    )


def render_plan_steps_start_view(
    view: PlanStepsStartView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把计划步骤开始视图转换为中立展示块。"""
    summary = (
        f"loops={view.loops} · steps={view.step_count} · "
        f"stop_on_fail={str(view.stop_on_fail).lower()}"
    )

    spans = [
        TextSpan("•", SUCCESS_DOT_STYLE),
        TextSpan(" Plan Steps", TITLE_STYLE),
    ]
    _append_layout_line(
        spans,
        [TextSpan(summary, PREVIEW_MORE_STYLE)],
        first_prefix=TextSpan("  └ ", PREVIEW_MORE_STYLE),
        continuation_prefix=TextSpan("    ", PREVIEW_MORE_STYLE),
        terminal_width=terminal_width,
        measure_width=measure_width,
    )

    for tool in view.tools:
        for line_index, line in enumerate(_content_lines(tool)):
            _append_layout_line(
                spans,
                [TextSpan(line, COMMAND_HEAD_STYLE)],
                first_prefix=TextSpan(
                    "    - " if line_index == 0 else "      ",
                    PREVIEW_TEXT_STYLE,
                ),
                continuation_prefix=TextSpan("      ", PREVIEW_TEXT_STYLE),
                terminal_width=terminal_width,
                measure_width=measure_width,
            )

    if view.omitted_steps:
        more = f"... {view.omitted_steps} more"
        _append_layout_line(
            spans,
            [TextSpan(more, PREVIEW_MORE_STYLE)],
            first_prefix=TextSpan("    ", PREVIEW_MORE_STYLE),
            continuation_prefix=TextSpan("    ", PREVIEW_MORE_STYLE),
            terminal_width=terminal_width,
            measure_width=measure_width,
        )

    return StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
        preserve_spans=True,
    )


def _append_layout_line(
    spans: list[TextSpan],
    parts: list[TextSpan],
    *,
    first_prefix: TextSpan,
    continuation_prefix: TextSpan,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None,
) -> None:
    """向计划块追加一条带悬挂缩进的逻辑行。"""
    rendered = layout_styled_line(
        parts,
        first_prefix=first_prefix,
        continuation_prefix=continuation_prefix,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    if not rendered:
        spans.append(TextSpan("\n"))
        return None
    first = rendered[0]
    spans.append(TextSpan(
        f"\n{first.text}",
        first.style,
        first.hyperlink,
    ))
    spans.extend(rendered[1:])


def _content_lines(value: typing.Any) -> list[str]:
    """清理展示文本并移除边界空白行。"""
    lines = sanitize_terminal_text(value).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _width_of(
    value: str,
    measure_width: typing.Callable[[str], int] | None,
) -> int:
    """返回指定前缀的终端显示宽度。"""
    return max(0, (measure_width or text_display_width)(value))


if __name__ == '__main__':
    pass
