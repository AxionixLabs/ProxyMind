# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

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


if __name__ == '__main__':
    pass
