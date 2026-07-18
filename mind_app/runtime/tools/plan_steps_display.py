# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_events.tool_traces.common import (
    COMMAND_HEAD_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)

PLAN_STEPS_PREVIEW_LIMIT = 8


def _part(text: str, style: str | None) -> dict[str, typing.Optional[str]]:
    """构造带样式的计划步骤文本片段。"""
    return {"text": text, "style": style}


def _plan_steps(arguments: typing.Any) -> list[dict[str, typing.Any]]:
    """读取 plan_steps 参数中的步骤列表。"""
    if not isinstance(arguments, dict):
        return []
    raw_steps = arguments.get("steps")
    if not isinstance(raw_steps, list):
        return []
    return [step for step in raw_steps if isinstance(step, dict)]


def _loops(arguments: typing.Any) -> int:
    """读取计划循环次数。"""
    if not isinstance(arguments, dict):
        return 1
    try:
        return max(1, int(arguments.get("loops") or 1))
    except (TypeError, ValueError):
        return 1


def _stop_on_fail(arguments: typing.Any) -> bool:
    """读取计划失败停止策略。"""
    if not isinstance(arguments, dict):
        return True
    return bool(arguments.get("stop_on_fail", True))


def render_plan_steps_start(
    arguments: typing.Any
) -> tuple[str, list[dict[str, typing.Optional[str]]]]:
    """将 plan_steps 参数渲染为静态执行计划块。"""
    steps = _plan_steps(arguments)
    loops = _loops(arguments)
    stop_on_fail = _stop_on_fail(arguments)
    summary = (
        f"loops={loops} · steps={len(steps)} · "
        f"stop_on_fail={str(stop_on_fail).lower()}"
    )

    text_lines = [
        "• Plan Steps",
        f"  └ {summary}"
    ]
    parts: list[dict[str, typing.Optional[str]]] = [
        _part("•", SUCCESS_DOT_STYLE),
        _part(" Plan Steps", TITLE_STYLE),
        _part("\n  └ ", PREVIEW_MORE_STYLE),
        _part(summary, PREVIEW_MORE_STYLE)
    ]

    for step in steps[:PLAN_STEPS_PREVIEW_LIMIT]:
        tool = str(step.get("tool") or "tool").strip() or "tool"
        text_lines.append(f"    - {tool}")
        parts.extend([
            _part("\n    - ", PREVIEW_TEXT_STYLE),
            _part(tool, COMMAND_HEAD_STYLE)
        ])

    omitted = max(0, len(steps) - PLAN_STEPS_PREVIEW_LIMIT)
    if omitted:
        more = f"... {omitted} more"
        text_lines.append(f"    {more}")
        parts.extend([
            _part("\n    ", PREVIEW_MORE_STYLE),
            _part(more, PREVIEW_MORE_STYLE)
        ])

    return "\n".join(text_lines), parts


if __name__ == '__main__':
    pass
