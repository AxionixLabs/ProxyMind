# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from mind_core.hook_trust import HookTrustState
from mind_core.hooks import HookEventName

ToolValue = typing.TypeVar("ToolValue")

HookPermissionAction = typing.Literal[
    "allow",
    "deny",
    "abstain",
]


@dataclass(frozen=True, slots=True)
class HookRuntimeEntry:
    """描述运行时中单个 Hook 的来源和激活状态。"""
    key: str
    event: HookEventName
    source_scope: str
    source_path: str | None
    content_hash: str
    enabled: bool
    trust_state: HookTrustState
    active: bool


@dataclass(frozen=True, slots=True)
class HookRuntimeStatus:
    """保存一个不可变 Hook 运行时状态视图。"""
    installed_count: int
    active_count: int
    hooks: tuple[HookRuntimeEntry, ...] = ()
    trust_error: str = ""


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
    decision: str = ""
    reason: str = ""
    updated_input: dict[str, typing.Any] | None = None
    additional_context: tuple[str, ...] = ()
    system_message: str = ""
    replacement_result: typing.Any = None
    replacement_result_set: bool = False
    continuation_prompt: str = ""
    suppress_original_output: bool = False

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
            "system_message",
            str(self.system_message or "").strip(),
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
    output: dict[str, typing.Any] = field(default_factory=dict)
    effect: HookOutputEffect = field(default_factory=HookOutputEffect)
    error: str = ""
    blocks_event: bool = False

    def __post_init__(self) -> None:
        """复制结构化输出，避免聚合期间被外部修改。"""
        object.__setattr__(self, "output", dict(self.output))

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
    system_message: str = ""

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
        object.__setattr__(self, "system_message", str(self.system_message or "").strip())


@dataclass(frozen=True, slots=True)
class HookPermissionDecision:
    """表示 Hook 链路对当前授权请求的三态决定。"""
    action: HookPermissionAction
    reason: str = ""
    hook_keys: tuple[str, ...] = ()
    updated_input: dict[str, typing.Any] | None = None

    @classmethod
    def abstain(cls) -> "HookPermissionDecision":
        """返回交由原审批链路处理的决定。"""
        return cls(action="abstain")

    def __post_init__(self) -> None:
        """复制可变输入，避免审批记录被外部修改。"""
        if self.updated_input is not None:
            object.__setattr__(self, "updated_input", dict(self.updated_input))


@dataclass(frozen=True, slots=True)
class SubagentStartResult:
    """保存子执行主体开始 Hook 注入的上下文。"""
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

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
        object.__setattr__(self, "system_message", str(self.system_message or "").strip())


@dataclass(frozen=True, slots=True)
class SubagentStopDecision:
    """表示停止 Hook 聚合后的子执行主体继续决定。"""
    should_continue: bool
    continuation_prompt: str = ""
    reason: str = ""
    hook_keys: tuple[str, ...] = ()
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

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
        object.__setattr__(self, "system_message", str(self.system_message or "").strip())


@dataclass(frozen=True, slots=True)
class TurnStartResult:
    """保存轮次开始 Hook 处理后的请求输入。"""
    message: str
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

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
        object.__setattr__(self, "system_message", str(self.system_message or "").strip())


@dataclass(frozen=True, slots=True)
class StopHookDecision:
    """表示轮次停止 Hook 聚合后的继续决定。"""
    should_continue: bool
    continuation_prompt: str = ""
    reason: str = ""
    hook_keys: tuple[str, ...] = ()
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

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
        object.__setattr__(self, "system_message", str(self.system_message or "").strip())


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """描述工具执行完成后的稳定结果快照。"""
    executed: bool
    ok: bool
    duration_ms: int
    result: typing.Any = None
    error: str = ""
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ToolCallRunResult(typing.Generic[ToolValue]):
    """保存 Hook 协调后的工具调用结果。"""
    allowed: bool
    value: ToolValue | None = None
    reason: str = ""
    replacement_result: typing.Any = None
    replacement_result_set: bool = False
    suppress_original_output: bool = False
    additional_context: tuple[str, ...] = ()
    system_message: str = ""

    def __post_init__(self) -> None:
        """规范化工具 Hook 附加反馈文本。"""
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
        object.__setattr__(self, "system_message", str(self.system_message or "").strip())


if __name__ == '__main__':
    pass
