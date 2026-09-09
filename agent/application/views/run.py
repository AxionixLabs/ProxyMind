# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field,
)

ContextCompactionPhase: typing.TypeAlias = typing.Literal[
    "pre_turn",
    "mid_turn",
    "standalone",
]

ContextCompactionTrigger: typing.TypeAlias = typing.Literal[
    "automatic",
    "manual",
]

ContextCompactionStatus: typing.TypeAlias = typing.Literal[
    "in_progress",
    "completed",
    "failed",
]


@dataclass(frozen=True, slots=True)
class RunStartedView:
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
class RunCompletedView:
    """描述一次非交互输出任务的完成信息。"""

    usage: dict[str, typing.Any]
    response_id: str = ""
    model: str = ""
    route: str = ""
    request_id: str = ""
    service_tier: str = ""
    stop_reason: str | None = None
    stop_sequence: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RunIncompleteView:
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
class FailureView:
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
class LifecycleView:
    """描述服务端生命周期事件的展示数据。"""

    text: str


@dataclass(frozen=True, slots=True)
class ContextCompactionView:
    """描述一项服务端上下文压缩事实及其展示边界坐标。"""

    turn_id: str
    item_id: str
    event_seq: int
    presentation_epoch: int
    status: ContextCompactionStatus
    phase: ContextCompactionPhase
    trigger: ContextCompactionTrigger
    reason: str
    error_type: str | None = None
    retryable: bool | None = None
    before_items: int | None = None
    after_items: int | None = None
    before_chars: int | None = None
    after_chars: int | None = None
    dropped_items: int | None = None
    reduction_ratio: float | None = None
    latency_ms: int | None = None
    replacement_version: int | None = None


if __name__ == '__main__':
    pass
