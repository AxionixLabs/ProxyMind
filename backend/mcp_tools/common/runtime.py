#  ____              _   _
# |  _ \ _   _ _ __ | |_(_)_ __ ___   ___
# | |_) | | | | '_ \| __| | '_ ` _ \ / _ \
# |  _ <| |_| | | | | |_| | | | | | |  __/
# |_| \_\\__,_|_| |_|\__|_|_| |_| |_|\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "runtime"})
    @task_middleware("sleep")
    async def sleep(delay: float) -> CallToolResult:
        """
        D: common
        C: runtime
        A: sleep
        P:
          delay: float
        R: CTR
        N:
          - 固定时间等待，用于节奏控制/动画缓冲
          - 仅时间延迟 ≠ 页面就绪（需要时应配合 wait_* 断言）
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "runtime"})
    @task_middleware("loop_steps")
    async def loop_steps(
        loops: int,
        steps: list[dict[str, typing.Any]],
        stop_on_fail: bool = True
    ) -> CallToolResult:
        """
        D: common
        C: runtime
        A: loop_steps
        P:
          loops: int
          steps: list[{"tool": str, "args": dict}]
          stop_on_fail: bool=True
        R: CTR
        N:
          - 全局“宏编排声明器”：仅做 loops/steps 校验与归一化，返回可执行声明（declaration），不执行 steps。
          - steps[].tool 是工具名；steps[].args 原样保留（包含 matrix 时也不改写）。
          - 禁止嵌套 loop_steps（steps 内不得出现 loop_steps）。
          - 实际循环执行由执行层/runner 读取 declaration 后完成；每一步由 step.args.matrix 自行分发到设备。
        """

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
