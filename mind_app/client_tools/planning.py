# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mcp import types as mcp_types
from mind_app.client_tools.result import client_tool_result
from mind_app.client_tools.types import (
    ClientTool,
    ClientToolRuntime
)

PLAN_STEPS_TOOL = "plan_steps"

PLAN_STEPS_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "loops": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": "循环执行 steps 的次数。"
        },
        "stop_on_fail": {
            "type": "boolean",
            "description": "为 true 时，任一步骤失败后停止后续计划执行。"
        },
        "steps": {
            "type": "array",
            "maxItems": 50,
            "description": "调用计划步骤。每项包含 tool 和 args，可选 meta。",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "tool": {
                        "type": "string",
                        "description": "要调用的工具名称，不允许为 plan_steps。"
                    },
                    "args": {
                        "type": "object",
                        "description": "传给工具的参数。"
                    },
                    "meta": {
                        "type": "object",
                        "description": "可选工具元数据，用于覆盖或补充工具声明。"
                    }
                },
                "required": ["tool", "args"]
            }
        }
    },
    "required": ["steps"],
    "additionalProperties": False
}


def normalize_plan_arguments(
    arguments: dict[str, typing.Any] | None
) -> tuple[bool, dict[str, typing.Any], list[str]]:
    """校验并标准化模型提交的步骤计划。"""
    payload = dict(arguments or {})
    errors: list[str] = []

    loops = _bounded_int(payload.get("loops"), default=1, minimum=1, maximum=50)
    stop_on_fail = bool(payload.get("stop_on_fail", True))

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
        errors.append("steps not list")
    if len(raw_steps) > 50:
        raw_steps = raw_steps[:50]
        errors.append("steps truncated to 50")
    if not raw_steps:
        errors.append("empty steps")

    steps: list[dict[str, typing.Any]] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            errors.append(f"steps[{index}] not dict")
            continue

        tool = str(raw_step.get("tool") or "").strip()
        if not tool:
            errors.append(f"steps[{index}] missing tool")
            continue
        if tool == PLAN_STEPS_TOOL:
            errors.append(f"steps[{index}] nested plan_steps forbidden")
            continue

        args = raw_step.get("args")
        if not isinstance(args, dict):
            args = {}
            errors.append(f"steps[{index}] args not dict")

        item: dict[str, typing.Any] = {
            "tool": tool,
            "args": dict(args)
        }
        if isinstance(raw_step.get("meta"), dict):
            item["meta"] = dict(raw_step["meta"])
        if isinstance(raw_step.get("execution"), dict):
            item["execution"] = dict(raw_step["execution"])

        steps.append(item)

    plan = {
        "loops": loops,
        "stop_on_fail": stop_on_fail,
        "steps": steps
    }
    ok = bool(steps) and not errors
    return ok, plan, errors


def planning_tools() -> list[ClientTool]:
    """返回 Mind 内置规划工具列表。"""

    async def plan_steps_handler(
        arguments: dict[str, typing.Any],
        runtime: ClientToolRuntime
    ) -> mcp_types.CallToolResult:
        """返回标准化计划声明。"""
        _ = runtime
        ok, plan, errors = normalize_plan_arguments(arguments)
        text = (
            f"plan declared loops={plan['loops']} steps={len(plan['steps'])} "
            f"stop_on_fail={plan['stop_on_fail']}"
        )
        if errors:
            text = f"{text}\nerrors={'; '.join(errors[:8])}"

        return client_tool_result(
            tool=PLAN_STEPS_TOOL,
            ok=ok,
            text=text,
            args=dict(arguments or {}),
            data={
                "executed": False,
                "plan": plan,
                "errors": errors
            }
        )

    return [
        ClientTool(
            name=PLAN_STEPS_TOOL,
            description=(
                "提交一个由 Mind 本地执行的工具调用计划。"
                " steps 中每项包含 tool 和 args，可选 meta；不允许嵌套 plan_steps。"
                " 该工具用于一次性声明多步计划，Mind 会按顺序执行并汇总结果。"
            ),
            input_schema=PLAN_STEPS_INPUT_SCHEMA,
            meta={
                "hidden": False,
                "domain": "mind",
                "class": "planning",
                "plan_executor": True
            },
            handler=plan_steps_handler,
        )
    ]


def _bounded_int(
    value: typing.Any,
    *,
    default: int,
    minimum: int,
    maximum: int
) -> int:
    """把输入值转换为受限整数。"""
    try:
        number = int(value if value is not None else default)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


if __name__ == '__main__':
    pass
