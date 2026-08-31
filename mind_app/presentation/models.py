# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
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
]

ApprovalSource = typing.Literal[
    "user",
    "hook",
    "policy",
    "auto_review",
]

ProgressSource = typing.Literal[
    "tool",
    "enhancement",
]

PatchPhase = typing.Literal[
    "proposed",
    "applied",
    "failed",
]

PatchAction = typing.Literal[
    "add",
    "delete",
    "update",
    "rename",
]

PatchLineKind = typing.Literal[
    "context",
    "add",
    "remove",
]

HookViewPhase = typing.Literal[
    "started",
    "completed",
]

HookViewStatus = typing.Literal[
    "running",
    "completed",
    "failed",
    "blocked",
    "stopped",
]

HookOutputKind = typing.Literal[
    "warning",
    "stop",
    "feedback",
    "context",
    "error",
]

@dataclass(frozen=True, slots=True)
class RunStartedView(object):
    """描述一次非交互输出任务的启动信息。"""
    thread_id: str
    turn_id: str
    session_id: str
    message: str
    model: str
    provider: str
    approval: str
    workdir: str
    sandbox: str
    reasoning_effort: str
    reasoning_summaries: str
    hook_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunCompletedView(object):
    """描述一次非交互输出任务的完成信息。"""
    usage: dict[str, typing.Any]
    response_id: str = ""
    model: str = ""
    route: str = ""
    request_id: str = ""
    service_tier: str = ""
    stop_reason: str | None = None
    stop_sequence: str | None = None


@dataclass(frozen=True, slots=True)
class RunIncompleteView(object):
    """描述一次未完整结束的模型运行。"""
    usage: dict[str, typing.Any]
    reason: str = ""
    can_continue: bool | None = None
    response_id: str = ""
    model: str = ""
    route: str = ""
    request_id: str = ""
    service_tier: str = ""
    stop_reason: str | None = None
    stop_sequence: str | None = None


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
class PatchLineView(object):
    """描述补丁中的一行及其新旧文件位置。"""
    kind: PatchLineKind
    text: str
    old_line: int | None = None
    new_line: int | None = None


@dataclass(frozen=True, slots=True)
class PatchHunkView(object):
    """描述补丁中连续的一组差异行。"""
    lines: tuple[PatchLineView, ...]


@dataclass(frozen=True, slots=True)
class PatchFileView(object):
    """描述单个文件的结构化补丁变化。"""
    action: PatchAction
    old_path: str
    new_path: str
    hunks: tuple[PatchHunkView, ...]
    added: int = 0
    removed: int = 0
    old_line_count: int = 0
    new_line_count: int = 0


@dataclass(frozen=True, slots=True)
class PatchDiagnosticView(object):
    """描述补丁失败时一个具名诊断字段。"""
    label: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatchView(object):
    """描述一次补丁调用在完整生命周期中的结构化展示。"""
    call_id: str
    phase: PatchPhase
    raw_patch: str
    files: tuple[PatchFileView, ...] = ()
    result_files: tuple[dict[str, typing.Any], ...] = ()
    diagnostics: tuple[PatchDiagnosticView, ...] = ()
    cost_ms: int | None = None


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
    usage: dict[str, typing.Any] = field(default_factory=dict)
    response_id: str = ""
    model: str = ""
    route: str = ""
    request_id: str = ""
    service_tier: str = ""
    stop_reason: str | None = None
    stop_sequence: str | None = None


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
class HookOutputView(object):
    """描述一次 Hook 运行产生的展示条目。"""
    kind: HookOutputKind
    text: str


@dataclass(frozen=True, slots=True)
class HookRunView(object):
    """描述一次 Hook 运行的结构化生命周期。"""
    id: str
    hook_key: str
    event: str
    phase: HookViewPhase
    status: HookViewStatus
    status_message: str = ""
    duration_ms: int | None = None
    entries: tuple[HookOutputView, ...] = ()


@dataclass(frozen=True, slots=True)
class ProgressView(object):
    """描述工具执行期间的可见进度。"""
    text: str
    source: ProgressSource
    tool_name: str


if __name__ == '__main__':
    pass
