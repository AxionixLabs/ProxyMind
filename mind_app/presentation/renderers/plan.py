# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from ..models import (
    PlanStepsStartView,
    PlanUpdateView,
    StyledBlock,
    TextSpan,
    TextStyle
)
from ..styles import (
    COMMAND_HEAD_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)
from ..terminal_text import sanitize_terminal_text

PLAN_SUMMARY_STYLE = TextStyle(
    foreground=PREVIEW_MORE_STYLE.foreground,
    bold=True,
    dim=True,
    italic=True,
)

PLAN_ACTIVE_BODY_STYLE   = TextStyle(foreground="#5EEAD4", bold=True)
PLAN_INACTIVE_BODY_STYLE = PREVIEW_TEXT_STYLE


def render_plan_update_view(view: PlanUpdateView) -> StyledBlock:
    """把计划更新视图转换为中立展示块。"""
    text_lines = ["• Updated Plan"]

    explanation = sanitize_terminal_text(view.explanation)

    spans = [
        TextSpan("•", SUCCESS_DOT_STYLE),
        TextSpan(" Updated Plan", TITLE_STYLE),
    ]

    if explanation:
        text_lines.append(f"  └ {explanation}")

        spans.extend((
            TextSpan("\n  └ ", PLAN_SUMMARY_STYLE),
            TextSpan(explanation, PLAN_SUMMARY_STYLE),
        ))

    for index, item in enumerate(view.items):
        step   = sanitize_terminal_text(item.step)
        icon   = "✔" if item.status == "completed" else "□"
        style  = PLAN_ACTIVE_BODY_STYLE if item.status == "in_progress" else PLAN_INACTIVE_BODY_STYLE
        indent = "    " if explanation or index else "  └ "

        text_lines.append(f"{indent}{icon} {step}")

        spans.extend((
            TextSpan(f"\n{indent}{icon} ", style),
            TextSpan(step, style),
        ))

    return StyledBlock(
        plain_text="\n".join(text_lines),
        spans=tuple(spans),
        preserve_spans=True,
    )


def render_plan_steps_start_view(view: PlanStepsStartView) -> StyledBlock:
    """把计划步骤开始视图转换为中立展示块。"""
    summary = (
        f"loops={view.loops} · steps={view.step_count} · "
        f"stop_on_fail={str(view.stop_on_fail).lower()}"
    )

    text_lines = ["• Plan Steps", f"  └ {summary}"]

    spans = [
        TextSpan("•", SUCCESS_DOT_STYLE),
        TextSpan(" Plan Steps", TITLE_STYLE),
        TextSpan("\n  └ ", PREVIEW_MORE_STYLE),
        TextSpan(summary, PREVIEW_MORE_STYLE),
    ]

    for tool in view.tools:
        display_tool = sanitize_terminal_text(tool)

        text_lines.append(f"    - {display_tool}")
        spans.extend((
            TextSpan("\n    - ", PREVIEW_TEXT_STYLE),
            TextSpan(display_tool, COMMAND_HEAD_STYLE),
        ))

    if view.omitted_steps:
        more = f"... {view.omitted_steps} more"

        text_lines.append(f"    {more}")
        spans.extend((
            TextSpan("\n    ", PREVIEW_MORE_STYLE),
            TextSpan(more, PREVIEW_MORE_STYLE),
        ))

    return StyledBlock(
        plain_text="\n".join(text_lines),
        spans=tuple(spans),
        preserve_spans=True,
    )


if __name__ == '__main__':
    pass
