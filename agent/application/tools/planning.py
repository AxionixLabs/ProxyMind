# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.definitions import ClientTool
from agent.application.tools.results import (
    LocalToolResult,
    client_tool_result,
)
from agent.application.tools.plan_update import UPDATE_PLAN_TOOL

PLAN_STEPS_TOOL = "plan_steps"

PLAN_STEPS_INPUT_SCHEMA: dict[str, typing.Any] = {
    "type": "object",
    "properties": {
        "loops": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "循环执行 steps 的次数，默认为 1。loops=1 时回传每个步骤的"
                "完整结果；loops>1 时仅回传数字统计和去重后的失败原因，"
                "不回传成功步骤的具体输出。"
            )
        },
        "stop_on_fail": {
            "type": "boolean",
            "description": "为 true 时，任一步骤失败后停止后续计划执行。"
        },
        "steps": {
            "type": "array",
            "description": "调用计划步骤。每项包含 tool 和 args。",
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
                    }
                },
                "required": ["tool", "args"]
            }
        }
    },
    "required": ["steps"],
    "additionalProperties": False
}


def _minimum_int(
    value: typing.Any,
    *,
    default: int,
    minimum: int
) -> int:
    """把输入值转换为具有最小值的整数。"""
    try:
        number = int(value if value is not None else default)
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def _normalize_step(
    index: int,
    raw_step: typing.Any,
    errors: list[str]
) -> dict[str, typing.Any] | None:
    """校验并标准化单个计划步骤。"""
    if not isinstance(raw_step, dict):
        errors.append(f"steps[{index}] not dict")
        return None

    tool = str(raw_step.get("tool") or "").strip()
    if not tool:
        errors.append(f"steps[{index}] missing tool")
        return None
    if tool in {PLAN_STEPS_TOOL, UPDATE_PLAN_TOOL}:
        errors.append(f"steps[{index}] nested {tool} forbidden")
        return None

    args = raw_step.get("args")
    if not isinstance(args, dict):
        args = {}
        errors.append(f"steps[{index}] args not dict")

    return {"tool": tool, "args": dict(args)}


def normalize_plan_arguments(
    arguments: dict[str, typing.Any] | None
) -> tuple[bool, dict[str, typing.Any], list[str]]:
    """校验并标准化模型提交的步骤计划。"""
    payload = dict(arguments or {})

    errors: list[str] = []

    loops        = _minimum_int(payload.get("loops"), default=1, minimum=1)
    stop_on_fail = bool(payload.get("stop_on_fail", True))

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
        errors.append("steps not list")
    if not raw_steps:
        errors.append("empty steps")

    steps: list[dict[str, typing.Any]] = []
    for index, raw_step in enumerate(raw_steps):
        step = _normalize_step(index, raw_step, errors)
        if step is not None:
            steps.append(step)

    plan = {
        "loops"        : loops,
        "stop_on_fail" : stop_on_fail,
        "steps"        : steps
    }
    ok = bool(steps) and not errors

    return ok, plan, errors


def planning_tools() -> list[ClientTool]:
    """返回内置规划工具列表。"""

    async def plan_steps_handler(
        arguments: dict[str, typing.Any],
        runtime: ToolHandlerContext
    ) -> LocalToolResult:
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
                "宏步骤循环器。提交一个按顺序执行的工具调用计划。"
                " steps 中每项包含 tool 和 args；不允许嵌套 plan_steps。"
                " loops=1 时回传每个步骤的完整结果；loops>1 时仅回传数字"
                "统计和去重后的失败原因，不回传成功步骤的具体输出。"
                " 需要读取步骤具体输出时使用 loops=1。"
            ),
            input_schema=PLAN_STEPS_INPUT_SCHEMA,
            meta={
                "hidden": False,
                "domain": "client",
                "class": "loop"
            },
            handler=plan_steps_handler,
        )
    ]


if __name__ == '__main__':
    pass
