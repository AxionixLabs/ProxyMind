# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext, Idle
from backend.utilities.broadcast import broadcast


DelayArg = typing.Annotated[
    float,
    Field(description="固定等待的秒数，支持小数秒。"),
]
LoopCountArg = typing.Annotated[
    int,
    Field(description="循环次数声明；工具内部会把值限制在 1 到 50 之间。"),
]
LoopStepsArg = typing.Annotated[
    list[dict[str, typing.Any]],
    Field(description="步骤声明列表。每项都应包含 `tool` 和 `args`，且不允许嵌套 `loop_steps`。"),
]
StopOnFailArg = typing.Annotated[
    bool,
    Field(description="供执行器读取的失败策略。为 true 时，后续真正执行时应在首个失败步骤后停止。"),
]


def bind(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "按给定秒数执行一次固定等待。"
            " 该工具只负责时间延迟，不判断页面、任务或设备是否已经就绪。"
            " 需要等待具体状态时，应改用对应领域的显式检查或等待工具。"
        ),
        meta={"hidden": False, "domain": "common", "class": "runtime"}
    )
    @task_middleware("sleep")
    async def sleep(delay: DelayArg) -> CallToolResult:
        args = {
            "delay" : delay
        }

        async def call(*_) -> None:
            job_id = await idle.job_begin(f"runtime.sleep", args=args)
            try:
                return await asyncio.sleep(delay)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="sleep",
            args=args,
            target_list=[None],
            call=call,
            overrides=None
        )

    @mcp.tool(
        description=(
            "声明一个可循环执行的步骤列表。"
            " 该工具只校验并返回标准化声明，不会真的执行 `steps`。"
            " `steps` 中每一项应包含 `tool` 和 `args`，且不允许嵌套 `loop_steps`。"
        ),
        meta={"hidden": False, "domain": "common", "class": "runtime"}
    )
    @task_middleware("loop_steps")
    async def loop_steps(
        loops: LoopCountArg,
        steps: LoopStepsArg,
        stop_on_fail: StopOnFailArg = True
    ) -> CallToolResult:
        args = {
            "loops"        : loops,
            "steps"        : steps,
            "stop_on_fail" : stop_on_fail
        }

        async def call(*_) -> dict:
            max_loops = 50
            max_steps = 50

            try:
                loops_i = int(args.get("loops") or 1)
            except (TypeError, ValueError):
                loops_i = 1
            if loops_i < 1: loops_i = 1
            if loops_i > max_loops: loops_i = max_loops

            stop_on_fail_i = bool(args.get("stop_on_fail", True))

            raw_steps = args.get("steps")
            if not isinstance(raw_steps, list):
                raw_steps = []

            if len(raw_steps) > max_steps:
                raw_steps = raw_steps[:max_steps]

            errors: list[str] = []
            normalized: list[dict[str, typing.Any]] = []

            if not raw_steps:
                errors.append("empty steps")

            for idx, step in enumerate(raw_steps):
                if not isinstance(step, dict):
                    errors.append(f"steps[{idx}] not dict")
                    continue

                tool = step.get("tool", "").strip()
                vals = step.get("args", {})

                if not tool:
                    errors.append(f"steps[{idx}] missing tool")
                    continue

                # 禁止嵌套：防递归
                if tool == "loop_steps":
                    errors.append(f"steps[{idx}] nested loop_steps forbidden")
                    continue

                if not isinstance(vals, dict):
                    errors.append(f"steps[{idx}] args not dict")
                    vals = {}

                normalized.append({"tool": tool, "args": vals})

            ok = (not errors) and bool(normalized)

            payload = {
                "ok"           : ok,
                "executed"     : False,
                "loops"        : loops_i,
                "stop_on_fail" : stop_on_fail_i,
                "steps"        : normalized,
                "errors"       : errors,
                "note"         : "declaration_only: runner_executes; per-step device routing via step.args.matrix"
            }

            text = (
                f"tool=loop_steps ok={ok} loops={loops_i} steps={len(normalized)} stop_on_fail={stop_on_fail_i}"
                + (f"\nerrors={'; '.join(errors[:8])}" if errors else "")
            )

            return {
                "text"        : text,
                "attachments" : [],
                "data"        : payload,
                "logs"        : []
            }

        return await broadcast(
            tool="loop_steps",
            args=args,
            target_list=[None],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
