# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

PlanStatus = typing.Literal[
    "pending",
    "in_progress",
    "completed"
]


@dataclass(frozen=True, slots=True)
class TracePreview(object):
    """保存完整预览、屏幕预览和省略行数。"""

    full: str          = ""
    screen: str        = ""
    omitted_lines: int = 0
    kind: str          = "text"


@dataclass(frozen=True, slots=True)
class TraceEntry(object):
    """保存一条可独立展示的工具轨迹。"""

    title: str
    preview: TracePreview
    ok: bool = True


@dataclass(frozen=True, slots=True)
class ToolStartView(object):
    """描述普通工具开始执行时的展示数据。"""

    name: str
    arguments: dict[str, typing.Any]
    title: str
    preview: TracePreview


@dataclass(frozen=True, slots=True)
class GenericToolResultView(object):
    """描述普通工具执行结果的展示数据。"""

    name: str
    text: str
    ok: bool
    title: str
    preview: TracePreview


@dataclass(frozen=True, slots=True)
class PlanItemView(object):
    """描述计划中的单个步骤。"""

    step: str
    status: PlanStatus


@dataclass(frozen=True, slots=True)
class PlanUpdateView(object):
    """描述计划更新的展示数据。"""

    explanation: str
    items: tuple[PlanItemView, ...]


@dataclass(frozen=True, slots=True)
class PlanStepsStartView(object):
    """描述计划步骤开始执行时的展示数据。"""

    loops: int
    stop_on_fail: bool
    step_count: int
    tools: tuple[str, ...]
    omitted_steps: int


@dataclass(frozen=True, slots=True)
class BatchCallView(object):
    """描述并行批次中的单个工具调用。"""

    name: str
    arguments: dict[str, typing.Any]


@dataclass(frozen=True, slots=True)
class BatchStartView(object):
    """描述并行工具开始执行时的展示数据。"""

    calls: tuple[BatchCallView, ...]


@dataclass(frozen=True, slots=True)
class BatchResultView(object):
    """描述并行批次中的单个工具结果。"""

    name: str
    ok: bool
    text: str


@dataclass(frozen=True, slots=True)
class BatchCompletedView(object):
    """描述并行工具执行完成时的展示数据。"""

    results: tuple[BatchResultView, ...]


if __name__ == '__main__':
    pass
