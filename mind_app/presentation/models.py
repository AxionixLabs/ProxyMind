# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_app.approval.models import ApprovalDecisionValue

PlanStatus = typing.Literal[
    "pending",
    "in_progress",
    "completed"
]

ApprovalDecision: typing.TypeAlias = ApprovalDecisionValue

ApprovalState = typing.Literal[
    "approved",
    "denied",
    "cancelled",
    "expired",
]

ApprovalSource = typing.Literal[
    "user",
    "hook",
    "policy",
]

ProgressSource = typing.Literal[
    "tool",
    "enhancement",
]


@dataclass(frozen=True, slots=True)
class TextStyle(object):
    """描述与终端实现无关的文本样式。"""
    foreground: str | None = None
    background: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False


@dataclass(frozen=True, slots=True)
class TextSpan(object):
    """保存一段文本及其中立样式。"""
    text: str
    style: TextStyle = TextStyle()


@dataclass(frozen=True, slots=True)
class StyledBlock(object):
    """保存一个结构化文本块及其纯文本表示。"""
    plain_text: str
    spans: tuple[TextSpan, ...] = ()
    preserve_spans: bool = False
    direct: bool = False


@dataclass(frozen=True, slots=True)
class RunStartedView(object):
    """描述一次非交互输出任务的启动信息。"""
    thread_id: str
    turn_id: str
    session_id: str
    message: str
    mode: str
    model: str
    provider: str
    approval: str
    workdir: str
    sandbox: str
    reasoning_effort: str
    reasoning_summaries: str


@dataclass(frozen=True, slots=True)
class RunCompletedView(object):
    """描述一次非交互输出任务的完成信息。"""
    usage: dict[str, typing.Any]


@dataclass(frozen=True, slots=True)
class TracePreview(object):
    """保存完整预览、屏幕预览和省略行数。"""
    full: str = ""
    screen: str = ""
    omitted_lines: int = 0
    kind: str = "text"


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
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class GenericToolResultView(object):
    """描述普通工具执行结果的展示数据。"""
    name: str
    text: str
    ok: bool
    title: str
    preview: TracePreview
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class NativeToolResultView(object):
    """描述原生编码工具执行结果的展示数据。"""
    name: str
    arguments: dict[str, typing.Any]
    ok: bool
    data: typing.Any
    cost_ms: int | None
    entries: tuple[TraceEntry, ...]
    call_id: str = ""


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


@dataclass(frozen=True, slots=True)
class FailureView(object):
    """描述运行失败时的展示数据。"""
    phase: str
    error: str


@dataclass(frozen=True, slots=True)
class LifecycleView(object):
    """描述服务端生命周期事件的展示数据。"""
    text: str


@dataclass(frozen=True, slots=True)
class ApprovalView(object):
    """描述工具审批结果的展示数据。"""
    approval: dict[str, typing.Any]
    decision: ApprovalDecision
    state: ApprovalState
    source: ApprovalSource = "user"


@dataclass(frozen=True, slots=True)
class ProgressView(object):
    """描述工具执行期间的可见进度。"""
    text: str
    source: ProgressSource
    tool_name: str


if __name__ == '__main__':
    pass
