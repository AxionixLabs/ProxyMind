# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_events.tool_traces.common import (
    PREVIEW_MORE_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)

PLAN_SUMMARY_STYLE       = PREVIEW_MORE_STYLE
PLAN_ACTIVE_BODY_STYLE   = "bold #5EEAD4"
PLAN_INACTIVE_BODY_STYLE = PREVIEW_TEXT_STYLE


def _part(
    text: str,
    style: str | None
) -> dict[str, typing.Optional[str]]:
    """构造带样式的计划文本片段。"""
    return {"text": text, "style": style}


def _normalized_plan_data(
    data: typing.Any
) -> tuple[str, list[dict[str, str]]] | None:
    """读取已校验的计划工具结果。"""
    if not isinstance(data, dict):
        return None

    explanation = data.get("explanation")
    if not isinstance(explanation, str):
        return None

    raw_plan = data.get("plan")
    if not isinstance(raw_plan, list) or not raw_plan:
        return None

    plan: list[dict[str, str]] = []

    for raw_item in raw_plan:
        if not isinstance(raw_item, dict):
            return None
        step = raw_item.get("step")
        status = raw_item.get("status")
        if not isinstance(step, str) or not step.strip():
            return None
        if status not in {"pending", "in_progress", "completed"}:
            return None
        plan.append({"step": step.strip(), "status": str(status)})

    return explanation.strip(), plan


def render_plan_update(
    data: typing.Any
) -> tuple[str, list[dict[str, typing.Optional[str]]]] | None:
    """将计划工具结果转换为终端文本和样式片段。"""
    normalized = _normalized_plan_data(data)
    if normalized is None:
        return None

    explanation, plan = normalized

    text_lines = ["• Updated Plan"]

    parts: list[dict[str, typing.Optional[str]]] = [
        _part("•", SUCCESS_DOT_STYLE),
        _part(" Updated Plan", TITLE_STYLE)
    ]

    if explanation:
        text_lines.append(f"  └ {explanation}")
        parts.extend([
            _part("\n  └ ", PLAN_SUMMARY_STYLE),
            _part(explanation, PLAN_SUMMARY_STYLE)
        ])

    indent = "    " if explanation else "  "

    for item in plan:
        status = item["status"]
        icon   = "✔" if status == "completed" else "□"

        style = (
            PLAN_ACTIVE_BODY_STYLE
            if status == "in_progress"
            else PLAN_INACTIVE_BODY_STYLE
        )

        text_lines.append(f"{indent}{icon} {item['step']}")

        parts.extend([
            _part(f"\n{indent}{icon} ", style),
            _part(item["step"], style)
        ])

    return "\n".join(text_lines), parts


if __name__ == '__main__':
    pass
