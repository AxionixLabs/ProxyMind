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
class HookExecutionRecord:
    """保存单个 Hook 的结构化执行结果。"""
    hook_key: str
    output: dict[str, typing.Any] = field(default_factory=dict)
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

    @classmethod
    def allow(cls) -> "HookDecision":
        """返回允许继续执行的决定。"""
        return cls(allowed=True)


@dataclass(frozen=True, slots=True)
class HookPermissionDecision:
    """表示 Hook 链路对当前授权请求的三态决定。"""
    action: HookPermissionAction
    reason: str = ""
    hook_keys: tuple[str, ...] = ()

    @classmethod
    def abstain(cls) -> "HookPermissionDecision":
        """返回交由原审批链路处理的决定。"""
        return cls(action="abstain")


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


if __name__ == '__main__':
    pass
