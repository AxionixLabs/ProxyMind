# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from backend.utilities.tool_result import ToolOutput


async def sleep_output(delay: float) -> ToolOutput:
    """执行固定等待并返回统一结果。"""
    await asyncio.sleep(delay)
    return ToolOutput(
        ok=True,
        text=f"sleep completed delay={delay}",
        data={"delay": delay}
    )


def loop_steps_output(
    *,
    loops: int,
    steps: list[dict[str, typing.Any]],
    stop_on_fail: bool
) -> ToolOutput:
    """校验并返回循环步骤声明结果。"""
    max_loops = 50
    max_steps = 50

    try:
        loops_i = int(loops or 1)
    except (TypeError, ValueError):
        loops_i = 1
    if loops_i < 1:
        loops_i = 1
    if loops_i > max_loops:
        loops_i = max_loops

    stop_on_fail_i = bool(stop_on_fail)

    raw_steps = steps
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

        if tool == "loop_steps":
            errors.append(f"steps[{idx}] nested loop_steps forbidden")
            continue

        if not isinstance(vals, dict):
            errors.append(f"steps[{idx}] args not dict")
            vals = {}

        normalized.append({"tool": tool, "args": vals})

    ok = (not errors) and bool(normalized)

    payload = {
        "executed"     : False,
        "loops"        : loops_i,
        "stop_on_fail" : stop_on_fail_i,
        "steps"        : normalized,
        "errors"       : errors,
        "note"         : "declaration_only: runner_executes; per-step device routing via step.args.serial"
    }

    text = (
        f"loops={loops_i} steps={len(normalized)} stop_on_fail={stop_on_fail_i}"
        + (f"\nerrors={'; '.join(errors[:8])}" if errors else "")
    )

    return ToolOutput(ok=ok, text=text, data=payload)


if __name__ == '__main__':
    pass
