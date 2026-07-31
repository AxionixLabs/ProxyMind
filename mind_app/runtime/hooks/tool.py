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
    fingerprints: tuple[str, ...]
    decision: HookDecision


@dataclass(frozen=True, slots=True)
class _PostToolUseResult:
    """保存工具后置 Hook 对模型可见结果的影响。"""
    replacement_result: typing.Any = None
    replacement_result_set: bool = False
    suppress_original_output: bool = False
    additional_context: tuple[str, ...] = ()
    system_message: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        """规范化反馈文本集合。"""
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
        object.__setattr__(
            self,
            "system_message",
            str(self.system_message or "").strip(),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())


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

        updated_input: dict[str, typing.Any] | None = None

        reasons: list[str]         = []
        denied_keys: list[str]     = []
        contexts: list[str]        = []
        system_messages: list[str] = []

        for record in dispatched.records:
            if not record.ok:
                if record.blocks_event:
                    denied_keys.append(record.hook_key)
                    reasons.append(_bounded_reason(f"hook failed: {record.error}"))
                continue

            effect = record.effect
            if not effect.continue_execution:
                denied_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    effect.reason or "tool use denied by hook"
                ))
                continue

            if effect.updated_input is not None:
                if updated_input is None:
                    updated_input = {}
                updated_input.update(effect.updated_input)

            contexts.extend(effect.additional_context)

            if effect.system_message:
                system_messages.append(effect.system_message)

        if denied_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(denied_keys),
            )
        return HookDecision(
            allowed=True,
            updated_input=updated_input,
            additional_context=tuple(contexts),
            system_message="\n\n".join(system_messages),
        )

    async def post_tool_use(
        self,
        invocation: ToolInvocation,
        outcome: ToolOutcome
    ) -> _PostToolUseResult:
        """执行工具后事件并聚合模型可见结果影响。"""
        self.scope.require_turn(invocation.turn)
        if not self.scope.has_matching("PostToolUse", invocation.name):
            return _PostToolUseResult()

        dispatched = await _dispatch_tool_event(
            self.scope,
            "PostToolUse",
            invocation,
            outcome=outcome,
        )

        contexts: list[str]            = []
        system_messages: list[str]     = []
        reasons: list[str]             = []
        replacement_result: typing.Any = None
        replacement_result_set         = False
        suppress_original_output       = False

        for record in dispatched.records:
            if not record.ok:
                continue

            effect = record.effect

            contexts.extend(effect.additional_context)
            if effect.system_message:
                system_messages.append(effect.system_message)
            if effect.reason:
                reasons.append(_bounded_reason(effect.reason))
            if effect.replacement_result_set:
                replacement_result = effect.replacement_result
                replacement_result_set = True
            if effect.suppress_original_output or not effect.continue_execution:
                suppress_original_output = True

        return _PostToolUseResult(
            replacement_result=replacement_result,
            replacement_result_set=replacement_result_set,
            suppress_original_output=suppress_original_output,
            additional_context=tuple(contexts),
            system_message="\n\n".join(system_messages),
            reason="; ".join(reason for reason in reasons if reason),
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

            decision = record.effect.decision
            if decision == "deny":
                denied_keys.append(record.hook_key)
                denied_reasons.append(_bounded_reason(
                    str(
                        record.effect.reason
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
            self._prepared[invocation.call_id] = _prepared_decision(
                invocation,
                decision,
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
                updated_input=pre_tool.updated_input,
            )

        permission = await self.events.permission_request(
            self.effective_invocation(invocation, pre_tool)
        )
        return HookPermissionDecision(
            action=permission.action,
            reason=permission.reason,
            hook_keys=permission.hook_keys,
            updated_input=pre_tool.updated_input,
        )

    async def run(
        self,
        invocation: ToolInvocation,
        operation: typing.Callable[[], typing.Awaitable[ToolValue]]
    ) -> ToolCallRunResult[ToolValue]:
        """执行前置决定、工具操作和后置 Hook。"""
        return await self.run_invocation(
            invocation,
            lambda _invocation: operation(),
        )

    async def run_invocation(
        self,
        invocation: ToolInvocation,
        operation: typing.Callable[
            [ToolInvocation],
            typing.Awaitable[ToolValue],
        ]
    ) -> ToolCallRunResult[ToolValue]:
        """执行前置决定、工具操作和后置 Hook。"""
        decision = await self._decision_for(invocation)
        if not decision.allowed:
            return ToolCallRunResult(
                allowed=False,
                reason=decision.reason or "tool use denied by hook",
            )

        effective_invocation = self.effective_invocation(invocation, decision)

        started_at = time.perf_counter()

        try:
            value = await operation(effective_invocation)
        except asyncio.CancelledError:
            await self.events.post_tool_use(
                effective_invocation,
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
                effective_invocation,
                ToolOutcome(
                    executed=True,
                    ok=False,
                    duration_ms=_duration_ms(started_at),
                    error=f"{type(error).__name__}: {error}",
                ),
            )
            raise

        post_result = await self.events.post_tool_use(
            effective_invocation,
            _outcome_from_value(value, duration_ms=_duration_ms(started_at)),
        )
        return ToolCallRunResult(
            allowed=True,
            value=value,
            reason=post_result.reason,
            replacement_result=post_result.replacement_result,
            replacement_result_set=post_result.replacement_result_set,
            suppress_original_output=post_result.suppress_original_output,
            additional_context=(
                *decision.additional_context,
                *post_result.additional_context,
            ),
            system_message=_join_text(
                decision.system_message,
                post_result.system_message,
            ),
        )

    async def _decision_for(self, invocation: ToolInvocation) -> HookDecision:
        """复用匹配的预执行决定，否则重新执行前置 Hook。"""
        prepared = self._prepared.pop(invocation.call_id, None)

        if (
            prepared is not None
            and _invocation_fingerprint(invocation) in prepared.fingerprints
        ):
            return prepared.decision

        return await self.events.pre_tool_use(invocation)

    @staticmethod
    def effective_invocation(
        invocation: ToolInvocation,
        decision: HookDecision
    ) -> ToolInvocation:
        """返回应用前置 Hook 参数改写后的调用快照。"""
        if decision.updated_input is None:
            return invocation
        return invocation.with_arguments(decision.updated_input)


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


def _prepared_decision(
    invocation: ToolInvocation,
    decision: HookDecision
) -> _PreparedDecision:
    """构建同时匹配原始和改写参数的前置决定缓存。"""
    fingerprints = [_invocation_fingerprint(invocation)]
    effective    = ToolCallCoordinator.effective_invocation(invocation, decision)

    effective_fingerprint = _invocation_fingerprint(effective)
    if effective_fingerprint not in fingerprints:
        fingerprints.append(effective_fingerprint)

    return _PreparedDecision(
        fingerprints=tuple(fingerprints),
        decision=decision,
    )


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
        "summary"   : encoded[:limit],
        "truncated" : True
    }


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回可安全回填的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _join_text(*values: str) -> str:
    """合并非空文本段。"""
    return "\n\n".join(
        text
        for value in values
        for text in [str(value or "").strip()]
        if text
    )


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
