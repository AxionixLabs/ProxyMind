# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from agent.domain.hooks import (
    HookEventName,
    HookTrustPolicy
)
from agent.domain.hook_trust import HookTrustState

ToolValue = typing.TypeVar("ToolValue")

HookPermissionAction = typing.Literal[
    "allow",
    "deny",
    "abstain",
]

HookRunStatus = typing.Literal[
    "running",
    "completed",
    "failed",
    "blocked",
    "stopped",
]

HookOutputEntryKind = typing.Literal[
    "warning",
    "stop",
    "feedback",
    "context",
    "error",
]


@dataclass(frozen=True, slots=True)
class HookOutputEntry:
    """保存一次 Hook 运行产生的展示条目。"""
    kind: HookOutputEntryKind
    text: str


@dataclass(frozen=True, slots=True)
class HookRunSummary:
    """保存一次 Hook 调用的不可变生命周期快照。"""
    id: str
    hook_key: str
    event: HookEventName
    status: HookRunStatus
    status_message: str = ""
    started_at: float = 0.0
    completed_at: float | None = None
    duration_ms: int | None = None
    entries: tuple[HookOutputEntry, ...] = ()

    def __post_init__(self) -> None:
        """规范化展示文本并冻结输出条目。"""
        object.__setattr__(
            self,
            "status_message",
            str(self.status_message or "").strip(),
        )
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True, slots=True)
class HookRuntimeEntry:
    """描述运行时中单个 Hook 的来源和激活状态。"""
    key: str
    event: HookEventName
    source_scope: str
    source_path: str | None
    trust_policy: HookTrustPolicy
    content_hash: str
    trust_state: HookTrustState
    enabled: bool
    active: bool


@dataclass(frozen=True, slots=True)
class HookRuntimeStatus:
    """保存一个不可变 Hook 运行时状态视图。"""
    installed_count: int
    active_count: int
    hooks: tuple[HookRuntimeEntry, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HookEventRequest:
    """描述一次不绑定具体生命周期领域的 Hook 分发请求。"""
    event: HookEventName
    payload: dict[str, typing.Any]
    match_value: str = ""
    diagnostics: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制可变输入，避免分发期间被外部修改。"""
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "diagnostics", dict(self.diagnostics))


@dataclass(frozen=True, slots=True)
class HookOutputEffect:
    """描述 Hook 输出对后续执行的统一影响。"""
    continue_execution: bool = True
    stop_requested: bool = False
    decision: str = ""
    reason: str = ""
    updated_input: dict[str, typing.Any] | None = None
    additional_context: tuple[str, ...] = ()
    warning: str = ""
    replacement_result: typing.Any = None
    replacement_result_set: bool = False
    continuation_prompt: str = ""

    def __post_init__(self) -> None:
        """复制可变输入并规范化文本集合。"""
        if self.updated_input is not None:
            object.__setattr__(self, "updated_input", dict(self.updated_input))

        contexts: list[str] = []
        for value in self.additional_context:
            if isinstance(value, str):
                text = value.strip()
                if text:
                    contexts.append(text)
        object.__setattr__(self, "additional_context", tuple(contexts))

        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "decision", str(self.decision or "").strip())
        object.__setattr__(
            self,
            "warning",
            str(self.warning or "").strip(),
        )
        object.__setattr__(
            self,
            "continuation_prompt",
            str(self.continuation_prompt or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class HookNormalizedOutput:
    """保存 Hook 原始结构化输出和统一影响模型。"""
    output: dict[str, typing.Any] = field(default_factory=dict)
    effect: HookOutputEffect = field(default_factory=HookOutputEffect)

    def __post_init__(self) -> None:
        """复制结构化输出，避免执行记录间共享可变对象。"""
        object.__setattr__(self, "output", dict(self.output))


@dataclass(frozen=True, slots=True)
class HookExecutionRecord:
    """保存单个 Hook 的结构化执行结果。"""
    hook_key: str
    completion_order: int = 0
    output: dict[str, typing.Any] = field(default_factory=dict)
    effect: HookOutputEffect = field(default_factory=HookOutputEffect)
    stderr: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        """复制结构化输出，避免聚合期间被外部修改。"""
        object.__setattr__(self, "output", dict(self.output))
        object.__setattr__(self, "stderr", str(self.stderr or "").strip())

    @property
    def ok(self) -> bool:
        """返回 Hook 命令及其输出协议是否有效。"""
        return not self.error


@dataclass(frozen=True, slots=True)
class HookDispatchResult:
    """保存一次生命周期事件中全部匹配 Hook 的执行记录。"""
    event: HookEventName
    records: tuple[HookExecutionRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class HookDecision:
    """表示前置 Hook 聚合后的工具执行决定。"""
    allowed: bool
    reason: str = ""
    hook_keys: tuple[str, ...] = ()
    updated_input: dict[str, typing.Any] | None = None
    additional_context: tuple[str, ...] = ()

    @classmethod
    def allow(cls) -> "HookDecision":
        """返回允许继续执行的决定。"""
        return cls(allowed=True)

    def __post_init__(self) -> None:
        """复制可变输入并规范化文本集合。"""
        if self.updated_input is not None:
            object.__setattr__(self, "updated_input", dict(self.updated_input))

        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class HookPermissionDecision:
    """表示 Hook 链路对当前授权请求的三态决定。"""
    action: HookPermissionAction
    reason: str = ""
    hook_keys: tuple[str, ...] = ()
    updated_input: dict[str, typing.Any] | None = None
    additional_context: tuple[str, ...] = ()

    @classmethod
    def abstain(cls) -> "HookPermissionDecision":
        """返回交由原审批链路处理的决定。"""
        return cls(action="abstain")

    def __post_init__(self) -> None:
        """复制可变输入，避免审批记录被外部修改。"""
        if self.updated_input is not None:
            object.__setattr__(self, "updated_input", dict(self.updated_input))

        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class SubagentStartResult:
    """保存子执行主体开始 Hook 注入的上下文。"""
    additional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """规范化注入文本。"""
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class SubagentStopDecision:
    """表示停止 Hook 聚合后的子执行主体继续决定。"""
    should_continue: bool
    continuation_prompt: str = ""
    reason: str = ""
    hook_keys: tuple[str, ...] = ()
    additional_context: tuple[str, ...] = ()

    @classmethod
    def stop(cls) -> "SubagentStopDecision":
        """返回结束当前子执行主体轮次的决定。"""
        return cls(should_continue=False)

    def __post_init__(self) -> None:
        """规范化子执行主体停止 Hook 返回的文本。"""
        object.__setattr__(
            self,
            "continuation_prompt",
            str(self.continuation_prompt or "").strip(),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class TurnStartResult:
    """保存轮次开始 Hook 处理后的请求输入。"""
    message: str
    additional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """规范化请求输入和注入文本。"""
        object.__setattr__(self, "message", str(self.message or ""))
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class StopHookDecision:
    """表示轮次停止 Hook 聚合后的继续决定。"""
    should_continue: bool
    continuation_prompt: str = ""
    reason: str = ""
    hook_keys: tuple[str, ...] = ()
    additional_context: tuple[str, ...] = ()

    @classmethod
    def stop(cls) -> "StopHookDecision":
        """返回结束当前模型轮次的决定。"""
        return cls(should_continue=False)

    def __post_init__(self) -> None:
        """规范化停止 Hook 返回的文本。"""
        object.__setattr__(
            self,
            "continuation_prompt",
            str(self.continuation_prompt or "").strip(),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """描述工具执行完成后的稳定结果快照。"""
    executed: bool
    ok: bool
    duration_ms: int
    result: typing.Any = None
    hook_response: typing.Any = None
    error: str = ""
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ToolResultSnapshot:
    """保存工具执行完成后的标准结果字段。"""
    ok: bool
    text: str
    fields: dict[str, typing.Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """复制结果字段并规范化文本。"""
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "fields", dict(self.fields))


@dataclass(frozen=True, slots=True)
class ToolOperationResult(typing.Generic[ToolValue]):
    """保存工具操作原始值、结果快照和内部反馈。"""
    value: ToolValue
    snapshot: ToolResultSnapshot
    hook_response: typing.Any = None
    additional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验结果快照并规范化内部反馈。"""
        if not isinstance(self.snapshot, ToolResultSnapshot):
            raise TypeError("tool result snapshot is required")
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class HookVisibleToolResult:
    """描述应用后置 Hook 后模型可见的工具结果。"""
    ok: bool
    text: str
    fields: dict[str, typing.Any] = field(default_factory=dict)
    additional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """复制可变字段并规范化反馈文本。"""
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "fields", dict(self.fields))
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolCallRunResult(typing.Generic[ToolValue]):
    """保存 Hook 协调后的工具调用结果。"""
    allowed: bool
    value: ToolValue | None = None
    visible_result: HookVisibleToolResult | None = None
    reason: str = ""
    additional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """规范化工具调用携带的附加上下文。"""
        object.__setattr__(
            self,
            "additional_context",
            tuple(
                text
                for value in self.additional_context
                for text in [str(value or "").strip()]
                if text
            ),
        )


if __name__ == '__main__':
    pass
