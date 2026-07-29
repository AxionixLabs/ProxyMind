# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
import asyncio
import hashlib
from dataclasses import dataclass
from mind_app.mcp.tool_result import normalize_call_tool_result
from mind_app.runtime.execution import ToolInvocation
from .models import (
    HookDecision,
    ToolCallRunResult,
    ToolOutcome
)
from .runtime import HookRuntime

ToolValue = typing.TypeVar("ToolValue")


@dataclass(frozen=True, slots=True)
class _PreparedDecision:
    """保存审批事件阶段已经执行的前置 Hook 决定。"""
    fingerprint: str
    decision: HookDecision


class ToolCallCoordinator:
    """协调工具调用的前置、执行和后置 Hook。"""

    def __init__(self, hooks: HookRuntime) -> None:
        self.hooks = hooks
        self._prepared: dict[str, _PreparedDecision] = {}

    async def prepare(self, invocation: ToolInvocation) -> HookDecision:
        """在审批前执行并缓存一次 PreToolUse 决定。"""
        decision = await self._decision_for(invocation)
        if invocation.call_id:
            self._prepared[invocation.call_id] = _PreparedDecision(
                fingerprint=_invocation_fingerprint(invocation),
                decision=decision,
            )
        return decision

    async def run(
        self,
        invocation: ToolInvocation,
        operation: typing.Callable[[], typing.Awaitable[ToolValue]]
    ) -> ToolCallRunResult[ToolValue]:
        """执行前置决定、工具操作和后置 Hook。"""
        decision = await self._decision_for(invocation)
        if not decision.allowed:
            return ToolCallRunResult(
                allowed=False,
                reason=decision.reason or "tool use denied by hook",
            )

        started_at = time.perf_counter()

        try:
            value = await operation()
        except asyncio.CancelledError:
            await self.hooks.post_tool_use(
                invocation,
                ToolOutcome(
                    executed=True,
                    ok=False,
                    duration_ms=_duration_ms(started_at),
                    error="tool execution cancelled",
                    cancelled=True,
                ),
            )
            raise

        except BaseException as error:
            await self.hooks.post_tool_use(
                invocation,
                ToolOutcome(
                    executed=True,
                    ok=False,
                    duration_ms=_duration_ms(started_at),
                    error=f"{type(error).__name__}: {error}",
                ),
            )
            raise

        await self.hooks.post_tool_use(
            invocation,
            _outcome_from_value(value, duration_ms=_duration_ms(started_at)),
        )
        return ToolCallRunResult(allowed=True, value=value)

    async def _decision_for(self, invocation: ToolInvocation) -> HookDecision:
        """复用匹配的预执行决定，否则重新执行前置 Hook。"""
        prepared = self._prepared.pop(invocation.call_id, None)

        if (
            prepared is not None
            and prepared.fingerprint == _invocation_fingerprint(invocation)
        ):
            return prepared.decision

        return await self.hooks.pre_tool_use(invocation)


def _invocation_fingerprint(invocation: ToolInvocation) -> str:
    """返回工具名和输入参数的稳定摘要。"""
    encoded = json.dumps(
        {
            "name": invocation.name,
            "arguments": invocation.arguments,
            "meta": invocation.meta,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def _duration_ms(started_at: float) -> int:
    """返回从指定时间点开始经过的毫秒数。"""
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _outcome_from_value(value: typing.Any, *, duration_ms: int) -> ToolOutcome:
    """把不同工具执行结果转换为稳定结果快照。"""
    if hasattr(value, "fields") and isinstance(getattr(value, "fields"), dict):
        return ToolOutcome(
            executed=True,
            ok=bool(getattr(value, "ok", True)),
            duration_ms=duration_ms,
            result=dict(value.fields),
        )

    if hasattr(value, "isError") and hasattr(value, "content"):
        normalized = normalize_call_tool_result(value)
        return ToolOutcome(
            executed=True,
            ok=normalized.ok,
            duration_ms=duration_ms,
            result=dict(normalized.fields),
        )

    if hasattr(value, "ok"):
        return ToolOutcome(
            executed=True,
            ok=bool(value.ok),
            duration_ms=duration_ms,
            result={
                "text": str(getattr(value, "text", "") or ""),
            },
        )

    return ToolOutcome(
        executed=True,
        ok=True,
        duration_ms=duration_ms,
        result=value,
    )


if __name__ == '__main__':
    pass
