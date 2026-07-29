# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

ToolValue = typing.TypeVar("ToolValue")


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
