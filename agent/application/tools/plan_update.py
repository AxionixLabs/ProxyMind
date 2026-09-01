# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.results import (
    LocalToolResult,
    client_tool_result,
)

UPDATE_PLAN_TOOL = "update_plan"

PLAN_STATUSES = {
    "pending",
    "in_progress",
    "completed"
}

UPDATE_PLAN_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "explanation": {
            "type": "string",
            "description": (
                "可选的计划调整摘要。仅在确有必要说明计划变化时填写；"
                "非空内容会显示在标题下方。"
            )
        },
        "plan": {
            "type": "array",
            "description": "完整计划快照。",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "step": {
                        "type": "string",
                        "description": "计划步骤。"
                    },
                    "status": {
                        "type": "string",
                        "enum": sorted(PLAN_STATUSES),
                        "description": "步骤当前状态。"
                    }
                },
                "required": ["step", "status"]
            }
        }
    },
    "required": ["plan"],
    "additionalProperties": False
}


def _normalize_explanation(
    value: typing.Any,
    errors: list[str]
) -> str:
    """校验并标准化计划说明。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        errors.append("explanation not string")
        return ""
    return value.strip()


def _normalize_plan_item(
    index: int,
    value: typing.Any,
    errors: list[str]
) -> dict[str, str] | None:
    """校验并标准化单个计划步骤。"""
    if not isinstance(value, dict):
        errors.append(f"plan[{index}] not object")
        return None

    raw_step = value.get("step")
    if not isinstance(raw_step, str) or not raw_step.strip():
        errors.append(f"plan[{index}] invalid step")
        return None

    raw_status = value.get("status")
    if not isinstance(raw_status, str) or raw_status not in PLAN_STATUSES:
        errors.append(f"plan[{index}] invalid status")
        return None

    return {
        "step": raw_step.strip(),
        "status": raw_status
    }


def normalize_update_plan_arguments(
    arguments: dict[str, typing.Any] | None
) -> tuple[bool, dict[str, typing.Any], list[str]]:
    """校验并标准化完整计划快照。"""
    payload: dict     = dict(arguments or {})
    errors: list[str] = []

    explanation = _normalize_explanation(payload.get("explanation"), errors)

    raw_plan = payload.get("plan")
    if not isinstance(raw_plan, list):
        raw_plan = []
        errors.append("plan not list")
    if not raw_plan:
        errors.append("empty plan")

    plan: list[dict[str, str]] = []
    for index, raw_item in enumerate(raw_plan):
        item = _normalize_plan_item(index, raw_item, errors)
        if item is not None:
            plan.append(item)

    in_progress_count = sum(
        1 for item in plan
        if item["status"] == "in_progress"
    )
    if in_progress_count > 1:
        errors.append("multiple in_progress steps")

    normalized = {
        "explanation" : explanation,
        "plan"        : plan
    }
    return bool(plan) and not errors, normalized, errors


def update_plan_tools() -> list[ClientTool]:
    """返回客户端内置计划更新工具。"""

    async def update_plan_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext
    ) -> LocalToolResult:
        """校验计划快照并返回结构化结果。"""
        _ = runtime

        ok, normalized, errors = normalize_update_plan_arguments(arguments)

        plan = normalized["plan"]

        counts = {
            status: sum(1 for item in plan if item["status"] == status)
            for status in sorted(PLAN_STATUSES)
        }

        if ok:
            text = (
                f"plan updated steps={len(plan)} "
                f"completed={counts['completed']} "
                f"in_progress={counts['in_progress']} "
                f"pending={counts['pending']}"
            )
        else:
            text = f"plan rejected errors={'; '.join(errors[:8])}"

        return client_tool_result(
            tool=UPDATE_PLAN_TOOL,
            ok=ok,
            text=text,
            args=dict(arguments or {}),
            data={
                **normalized,
                "counts": counts,
                "errors": errors[:8]
            }
        )

    return [
        ClientTool(
            name=UPDATE_PLAN_TOOL,
            description=(
                "更新当前工作计划。每次提交完整计划快照；"
                "步骤状态只能是 pending、in_progress 或 completed，"
                "并且最多一个步骤处于 in_progress。"
            ),
            input_schema=UPDATE_PLAN_INPUT_SCHEMA,
            meta={
                "hidden": False,
                "domain": "client",
                "class": "plan"
            },
            handler=update_plan_handler
        )
    ]


if __name__ == '__main__':
    pass
