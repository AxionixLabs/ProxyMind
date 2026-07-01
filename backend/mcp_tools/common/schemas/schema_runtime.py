# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

DelayArg = typing.Annotated[
    float,
    Field(description="固定等待的秒数，支持小数秒。")
]
LoopCountArg = typing.Annotated[
    int,
    Field(description="循环次数声明；工具内部会把值限制在 1 到 50 之间。")
]
LoopStepsArg = typing.Annotated[
    list[dict[str, typing.Any]],
    Field(description="步骤声明列表。每项都应包含 `tool` 和 `args`，且不允许嵌套 `loop_steps`。")
]
StopOnFailArg = typing.Annotated[
    bool,
    Field(description="供执行器读取的失败策略。为 true 时，后续真正执行时应在首个失败步骤后停止。")
]


if __name__ == '__main__':
    pass
