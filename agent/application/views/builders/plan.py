# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.views import (
    PlanItemView,
    PlanStatus,
    PlanStepsStartView,
    PlanUpdateView,
)

PLAN_STEPS_PREVIEW_LIMIT = 8


def build_plan_update_view(data: typing.Any) -> PlanUpdateView | None:
    """构建计划更新的结构化展示数据。"""
    if not isinstance(data, dict):
        return None

    explanation = data.get("explanation")
    if not isinstance(explanation, str):
        return None

    raw_plan = data.get("plan")
    if not isinstance(raw_plan, list) or not raw_plan:
        return None

    items: list[PlanItemView] = []

    for raw_item in raw_plan:
        if not isinstance(raw_item, dict):
            return None

        step = raw_item.get("step")
        status = raw_item.get("status")

        if not isinstance(step, str) or not step.strip():
            return None
        if status == "pending":
            normalized_status: PlanStatus = "pending"
        elif status == "in_progress":
            normalized_status = "in_progress"
        elif status == "completed":
            normalized_status = "completed"
        else:
            return None

        items.append(PlanItemView(
            step=step.strip(),
            status=normalized_status,
        ))

    return PlanUpdateView(
        explanation=explanation.strip(),
        items=tuple(items),
    )


def build_plan_steps_start_view(arguments: typing.Any) -> PlanStepsStartView:
    """构建计划步骤开始执行时的结构化展示数据。"""
    steps = _plan_steps(arguments)
    tools = tuple(
        str(step.get("tool") or "tool").strip() or "tool"
        for step in steps[:PLAN_STEPS_PREVIEW_LIMIT]
    )

    return PlanStepsStartView(
        loops=_loops(arguments),
        stop_on_fail=_stop_on_fail(arguments),
        step_count=len(steps),
        tools=tools,
        omitted_steps=max(0, len(steps) - PLAN_STEPS_PREVIEW_LIMIT),
    )


def _plan_steps(arguments: typing.Any) -> list[dict[str, typing.Any]]:
    """读取计划参数中的有效步骤。"""
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


if __name__ == '__main__':
    pass
