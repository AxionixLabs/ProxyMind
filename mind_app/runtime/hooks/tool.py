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
    HookDispatchResult,
    HookPermissionDecision,
    ToolCallRunResult,
    ToolOutcome
)
from .scope import HookExecutionScope

ToolValue = typing.TypeVar("ToolValue")


@dataclass(frozen=True, slots=True)
class _PreparedDecision:
    """保存审批事件阶段已经执行的前置 Hook 决定。"""
    fingerprint: str
    decision: HookDecision


class ToolHookEvents:
    """构建并聚合工具生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def pre_tool_use(self, invocation: ToolInvocation) -> HookDecision:
        """执行工具前事件并聚合阻止决定。"""
        self.scope.require_turn(invocation.turn)
        if not self.scope.has_matching("PreToolUse", invocation.name):
            return HookDecision.allow()

        dispatched = await _dispatch_tool_event(
            self.scope,
            "PreToolUse",
            invocation,
        )

        reasons: list[str]     = []
        denied_keys: list[str] = []

        for record in dispatched.records:
            if not record.ok:
                if record.blocks_event:
                    denied_keys.append(record.hook_key)
                    reasons.append(_bounded_reason(f"hook failed: {record.error}"))
                continue

            output = record.output

            denied = (
                output.get("decision") in {"deny", "block"}
                or output.get("continue") is False
            )
            if denied:
                denied_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    str(output.get("reason") or "tool use denied by hook")
                ))

        if denied_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(denied_keys),
            )
        return HookDecision.allow()

    async def post_tool_use(
        self,
        invocation: ToolInvocation,
        outcome: ToolOutcome
    ) -> None:
        """执行工具后事件并忽略非阻断失败。"""
        self.scope.require_turn(invocation.turn)
        if not self.scope.has_matching("PostToolUse", invocation.name):
            return None

        await _dispatch_tool_event(
            self.scope,
            "PostToolUse",
            invocation,
            outcome=outcome,
        )

    async def permission_request(
        self,
        invocation: ToolInvocation
    ) -> HookPermissionDecision:
        """执行工具授权事件并聚合三态决定。"""
        self.scope.require_turn(invocation.turn)
        if not self.scope.has_matching(
            "PermissionRequest",
            invocation.name,
        ):
            return HookPermissionDecision.abstain()

        dispatched = await _dispatch_tool_event(
            self.scope,
            "PermissionRequest",
            invocation,
        )

        denied_keys: list[str]    = []
        denied_reasons: list[str] = []
        allowed_keys: list[str]   = []

        for record in dispatched.records:
            if not record.ok:
                if record.blocks_event:
                    denied_keys.append(record.hook_key)
                    denied_reasons.append(_bounded_reason(
                        f"hook failed: {record.error}"
                    ))
                continue

            decision = record.output.get("decision")
            if decision == "deny":
                denied_keys.append(record.hook_key)
                denied_reasons.append(_bounded_reason(
                    str(
                        record.output.get("reason")
                        or "permission denied by hook"
                    )
                ))
            elif decision == "allow":
                allowed_keys.append(record.hook_key)

        if denied_keys:
            return HookPermissionDecision(
                action="deny",
                reason="; ".join(denied_reasons),
                hook_keys=tuple(denied_keys),
            )
        if allowed_keys:
            return HookPermissionDecision(
                action="allow",
                hook_keys=tuple(allowed_keys),
            )
        return HookPermissionDecision.abstain()


class ToolCallCoordinator:
    """协调工具调用的前置、执行和后置 Hook。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.events = ToolHookEvents(scope)

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

    async def prepare_permission(
        self,
        invocation: ToolInvocation
    ) -> HookPermissionDecision:
        """按工具前置和授权顺序聚合审批 Hook 决定。"""
        pre_tool = await self.prepare(invocation)

        if not pre_tool.allowed:
            return HookPermissionDecision(
                action="deny",
                reason=pre_tool.reason,
                hook_keys=pre_tool.hook_keys,
            )

        return await self.events.permission_request(invocation)

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
            await self.events.post_tool_use(
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
            await self.events.post_tool_use(
                invocation,
                ToolOutcome(
                    executed=True,
                    ok=False,
                    duration_ms=_duration_ms(started_at),
                    error=f"{type(error).__name__}: {error}",
                ),
            )
            raise

        await self.events.post_tool_use(
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

        return await self.events.pre_tool_use(invocation)


async def _dispatch_tool_event(
    scope: HookExecutionScope,
    event: typing.Literal[
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
    ],
    invocation: ToolInvocation,
    *,
    outcome: ToolOutcome | None = None
) -> HookDispatchResult:
    """分发不包含内部执行授权的工具事件。"""
    payload: dict[str, typing.Any] = {
        "call_id": invocation.call_id,
        "tool_name": invocation.name,
        "tool_kind": _tool_kind(invocation.meta),
        "tool_input": dict(invocation.arguments),
    }

    if outcome is not None:
        payload["tool_outcome"] = {
            "executed": outcome.executed,
            "ok": outcome.ok,
            "duration_ms": outcome.duration_ms,
            "result": _bounded_result(outcome.result),
            "error": outcome.error,
            "cancelled": outcome.cancelled,
        }

    return await scope.dispatch(
        event,
        payload=payload,
        match_value=invocation.name,
        diagnostics={
            "tool": invocation.name,
            "call_id": invocation.call_id,
        },
    )


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


def _tool_kind(meta: dict[str, typing.Any] | None) -> str:
    """从工具元数据中读取稳定类别。"""
    if not isinstance(meta, dict):
        return "local"
    return str(meta.get("domain") or meta.get("class") or "local").strip() or "local"


def _bounded_result(value: typing.Any, limit: int = 32768) -> typing.Any:
    """返回适合 Hook 输入的有界结果。"""
    try:
        encoded = json.dumps(value, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) <= limit:
        return value

    return {
        "summary": encoded[:limit],
        "truncated": True,
    }


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回可安全回填的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


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
