# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.stream_events.tool_traces.common import (
    COMMAND_HEAD_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)
from ..models import (
    PlanStepsStartView,
    PlanUpdateView
)
from .models import (
    DisplayPart,
    RenderedBlock
)

PLAN_SUMMARY_STYLE       = f"bold italic {PREVIEW_MORE_STYLE}"
PLAN_ACTIVE_BODY_STYLE   = f"bold #5EEAD4"
PLAN_INACTIVE_BODY_STYLE = PREVIEW_TEXT_STYLE


def render_plan_update_view(view: PlanUpdateView) -> RenderedBlock:
    """把计划更新视图转换为当前终端展示。"""
    text_lines = ["• Updated Plan"]
    parts: list[DisplayPart] = [
        _part("•", SUCCESS_DOT_STYLE),
        _part(" Updated Plan", TITLE_STYLE),
    ]

    if view.explanation:
        text_lines.append(f"  └ {view.explanation}")
        parts.extend([
            _part("\n  └ ", PLAN_SUMMARY_STYLE),
            _part(view.explanation, PLAN_SUMMARY_STYLE),
        ])

    indent = "    " if view.explanation else "  "

    for item in view.items:
        icon = "✔" if item.status == "completed" else "□"
        style = (
            PLAN_ACTIVE_BODY_STYLE
            if item.status == "in_progress"
            else PLAN_INACTIVE_BODY_STYLE
        )

        text_lines.append(f"{indent}{icon} {item.step}")
        parts.extend([
            _part(f"\n{indent}{icon} ", style),
            _part(item.step, style),
        ])

    return RenderedBlock(
        text="\n".join(text_lines),
        display_parts=tuple(parts),
        preserve_display_parts=True,
    )


def render_plan_steps_start_view(view: PlanStepsStartView) -> RenderedBlock:
    """把计划步骤开始视图转换为当前终端展示。"""
    summary = (
        f"loops={view.loops} · steps={view.step_count} · "
        f"stop_on_fail={str(view.stop_on_fail).lower()}"
    )
    text_lines = [
        "• Plan Steps",
        f"  └ {summary}",
    ]
    parts: list[DisplayPart] = [
        _part("•", SUCCESS_DOT_STYLE),
        _part(" Plan Steps", TITLE_STYLE),
        _part("\n  └ ", PREVIEW_MORE_STYLE),
        _part(summary, PREVIEW_MORE_STYLE),
    ]

    for tool in view.tools:
        text_lines.append(f"    - {tool}")
        parts.extend([
            _part("\n    - ", PREVIEW_TEXT_STYLE),
            _part(tool, COMMAND_HEAD_STYLE),
        ])

    if view.omitted_steps:
        more = f"... {view.omitted_steps} more"
        text_lines.append(f"    {more}")
        parts.extend([
            _part("\n    ", PREVIEW_MORE_STYLE),
            _part(more, PREVIEW_MORE_STYLE),
        ])

    return RenderedBlock(
        text="\n".join(text_lines),
        display_parts=tuple(parts),
        preserve_display_parts=True,
    )


def _part(text: str, style: str | None) -> DisplayPart:
    """构造带样式的终端文本片段。"""
    return {"text": text, "style": style}


if __name__ == '__main__':
    pass
