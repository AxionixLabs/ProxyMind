# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

PlanStatus: typing.TypeAlias = typing.Literal[
    "pending",
    "in_progress",
    "completed",
]


@dataclass(frozen=True, slots=True)
class PlanItemView:
    """描述计划中的单个步骤。"""

    step: str
    status: PlanStatus


@dataclass(frozen=True, slots=True)
class PlanUpdateView:
    """描述计划更新的展示数据。"""

    explanation: str
    items: tuple[PlanItemView, ...]


@dataclass(frozen=True, slots=True)
class PlanStepsStartView:
    """描述计划步骤开始执行时的展示数据。"""

    loops: int
    stop_on_fail: bool
    step_count: int
    tools: tuple[str, ...]
    omitted_steps: int


if __name__ == '__main__':
    pass
