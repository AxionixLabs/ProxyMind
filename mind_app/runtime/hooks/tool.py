# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
import asyncio
import hashlib
from dataclasses import dataclass
from mind_app.runtime.execution import ToolInvocation
from mind_app.history.contracts import TranscriptSink
from .matching import hook_tool_name
from .results import apply_tool_result_effect
from .models import (
    HookDecision,
    HookDispatchResult,
    HookPermissionDecision,
    ToolCallRunResult,
    ToolOperationResult,
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
    stopped: bool = False
    blocked: bool = False
    feedback_message: str = ""
    replacement_result: typing.Any = None
    replacement_result_set: bool = False
    additional_context: tuple[str, ...] = ()

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
            "feedback_message",
            str(self.feedback_message or "").strip(),
        )


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
        contexts: list[str]    = []

        for record in dispatched.records:
            if not record.ok:
                continue

            effect = record.effect
            contexts.extend(effect.additional_context)

            if not effect.continue_execution:
                denied_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    effect.reason or "tool use denied by hook"
                ))
                continue

        if denied_keys:
            return HookDecision(
                allowed=False,
                reason=reasons[0],
                hook_keys=tuple(denied_keys),
                additional_context=tuple(contexts),
            )

        updated_records = tuple(
            record
            for record in dispatched.records
            if record.ok and record.effect.updated_input is not None
        )

        updated_input = (
            max(
                updated_records,
                key=lambda item: item.completion_order,
            ).effect.updated_input
            if updated_records
            else None
        )

        return HookDecision(
            allowed=True,
            updated_input=updated_input,
            additional_context=tuple(contexts),
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

        contexts: list[str] = []
        feedback: list[str] = []

        replacement_result: typing.Any = None
        replacement_result_set         = False

        stopped: bool = False
        blocked: bool = False

        for record in dispatched.records:
            if not record.ok:
                continue

            effect = record.effect

            contexts.extend(effect.additional_context)
            if effect.replacement_result_set:
                replacement_result = effect.replacement_result
                replacement_result_set = True

            if effect.stop_requested:
                stopped = True
                feedback.append(_bounded_reason(
                    effect.reason or "PostToolUse hook stopped execution"
                ))
            elif effect.decision == "block":
                blocked = True
                feedback.append(_bounded_reason(effect.reason))

        return _PostToolUseResult(
            stopped=stopped,
            blocked=blocked,
            feedback_message="\n\n".join(
                message for message in feedback if message
            ),
            replacement_result=replacement_result,
            replacement_result_set=replacement_result_set,
            additional_context=tuple(contexts),
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

    def __init__(
        self,
        scope: HookExecutionScope,
        transcript: TranscriptSink | None = None
    ) -> None:
        self.events     = ToolHookEvents(scope)
        self.transcript = transcript

        self._prepared: dict[str, _PreparedDecision] = {}

        self._recorded_calls: set[str] = set()
        self._finished_calls: set[str] = set()

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
                additional_context=pre_tool.additional_context,
            )

        permission = await self.events.permission_request(
            self.effective_invocation(invocation, pre_tool)
        )
        return HookPermissionDecision(
            action=permission.action,
            reason=permission.reason,
            hook_keys=permission.hook_keys,
            updated_input=pre_tool.updated_input,
            additional_context=pre_tool.additional_context,
        )

    async def run_invocation(
        self,
        invocation: ToolInvocation,
        operation: typing.Callable[
            [ToolInvocation],
            typing.Awaitable[ToolOperationResult[ToolValue]],
        ]
    ) -> ToolCallRunResult[ToolValue]:
        """执行前置决定、工具操作和后置 Hook。"""
        decision = await self._decision_for(invocation)
        if not decision.allowed:
            self.record_rejected(
                invocation,
                decision.reason or "tool use denied by hook",
            )
            return ToolCallRunResult(
                allowed=False,
                reason=decision.reason or "tool use denied by hook",
                additional_context=decision.additional_context,
            )

        effective_invocation = self.effective_invocation(invocation, decision)

        started_at = time.perf_counter()

        try:
            operation_result = await operation(effective_invocation)
            if not isinstance(operation_result, ToolOperationResult):
                raise TypeError("tool operation must return ToolOperationResult")
        except asyncio.CancelledError:
            outcome = ToolOutcome(
                executed=True,
                ok=False,
                duration_ms=_duration_ms(started_at),
                error="tool execution cancelled",
                cancelled=True,
            )

            self._record_outcome(effective_invocation, outcome)
            raise

        except BaseException as error:
            outcome = ToolOutcome(
                executed=True,
                ok=False,
                duration_ms=_duration_ms(started_at),
                error=f"{type(error).__name__}: {error}",
            )

            self._record_outcome(effective_invocation, outcome)
            raise

        outcome = ToolOutcome(
            executed=True,
            ok=operation_result.snapshot.ok,
            duration_ms=_duration_ms(started_at),
            result=dict(operation_result.snapshot.fields),
        )

        self._record_outcome(effective_invocation, outcome)

        post_result = _PostToolUseResult()
        if outcome.ok:
            post_result = await self.events.post_tool_use(
                effective_invocation,
                outcome,
            )

        additional_context = (
            *decision.additional_context,
            *operation_result.additional_context,
            *post_result.additional_context,
        )

        visible_result = apply_tool_result_effect(
            ok=operation_result.snapshot.ok,
            text=operation_result.snapshot.text,
            fields=operation_result.snapshot.fields,
            replacement_result=post_result.replacement_result,
            replacement_result_set=post_result.replacement_result_set,
            blocked=post_result.blocked,
            feedback_message=post_result.feedback_message,
            additional_context=additional_context,
            system_message=operation_result.system_message,
        )

        return ToolCallRunResult(
            allowed=True,
            value=operation_result.value,
            visible_result=visible_result,
        )

    def record_rejected(
        self,
        invocation: ToolInvocation,
        reason: str,
        *,
        result: typing.Any = None,
    ) -> None:
        """记录未执行的工具调用及其拒绝原因。"""
        self._record_start(invocation)

        self._record_outcome(
            invocation,
            ToolOutcome(
                executed=False,
                ok=False,
                duration_ms=0,
                error=str(reason or "tool execution rejected"),
                result=result,
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

        self._record_start(invocation)

        return await self.events.pre_tool_use(invocation)

    def _record_start(self, invocation: ToolInvocation) -> None:
        """在前置 Hook 前记录一次工具调用。"""
        key = invocation.call_id or _invocation_fingerprint(invocation)
        if key in self._recorded_calls:
            return None

        self._recorded_calls.add(key)

        if self.transcript is not None:
            self.transcript.append(
                "tool.started",
                actor="tool",
                payload={
                    "call_id": invocation.call_id,
                    "name": invocation.name,
                    "arguments": dict(invocation.arguments),
                },
            )

    def _record_outcome(
        self,
        invocation: ToolInvocation,
        outcome: ToolOutcome,
    ) -> None:
        """在后置 Hook 前记录一次工具结果。"""
        key = invocation.call_id or _invocation_fingerprint(invocation)
        if key in self._finished_calls:
            return None
        self._finished_calls.add(key)

        if self.transcript is None:
            return None

        payload: dict[str, typing.Any] = {
            "call_id": invocation.call_id,
            "name": invocation.name,
            "arguments": dict(invocation.arguments),
            "executed": outcome.executed,
            "ok": outcome.ok,
            "duration_ms": outcome.duration_ms,
        }
        if outcome.result is not None:
            payload["result"] = outcome.result
        if outcome.error:
            payload["error"] = outcome.error
        if outcome.cancelled:
            payload["cancelled"] = True

        self.transcript.append(
            "tool.completed" if outcome.ok else "tool.failed",
            actor="tool",
            payload=payload,
        )

    @staticmethod
    def effective_invocation(
        invocation: ToolInvocation,
        decision: HookDecision
    ) -> ToolInvocation:
        """返回应用前置 Hook 参数改写后的调用快照。"""
        if decision.updated_input is None:
            return invocation

        return invocation.with_arguments(_updated_tool_arguments(
            invocation,
            decision.updated_input,
        ))


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
        "tool_name": hook_tool_name(invocation.name),
        "tool_input": _hook_tool_input(invocation),
    }

    if event != "PermissionRequest":
        payload["tool_use_id"] = invocation.call_id
    if event == "PostToolUse":
        if outcome is None:
            raise ValueError("PostToolUse requires a tool outcome")
        payload["tool_response"] = _tool_response(outcome)

    return await scope.dispatch(
        event,
        payload=payload,
        match_value=invocation.name,
        diagnostics={
            "tool": invocation.name,
            "call_id": invocation.call_id,
        },
    )


def _hook_tool_input(invocation: ToolInvocation) -> dict[str, typing.Any]:
    """返回工具 Hook stdin 使用的稳定参数对象。"""
    arguments = invocation.arguments

    if invocation.name in {"shell_command", "exec_command"}:
        return {"command": str(arguments.get("command") or "")}
    if invocation.name == "apply_patch":
        return {"command": str(arguments.get("patch") or "")}

    return dict(arguments)


def _updated_tool_arguments(
    invocation: ToolInvocation,
    updated_input: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """把 Hook 参数对象转换回本地工具参数。"""
    if invocation.name in {"shell_command", "exec_command"}:
        command = updated_input.get("command")
        if not isinstance(command, str):
            raise ValueError("shell Hook updatedInput.command must be a string")
        return {**invocation.arguments, "command": command}

    if invocation.name == "apply_patch":
        command = updated_input.get("command")
        if not isinstance(command, str):
            raise ValueError(
                "apply_patch Hook updatedInput.command must be a string"
            )
        return {**invocation.arguments, "patch": command}

    return dict(updated_input)


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


def _tool_response(outcome: ToolOutcome) -> typing.Any:
    """返回工具后置事件使用的模型可见结果。"""
    if outcome.result is not None:
        return _bounded_result(outcome.result)
    if outcome.error:
        return {
            "error": outcome.error,
            "cancelled": outcome.cancelled,
        }

    return None


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回可安全回填的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _duration_ms(started_at: float) -> int:
    """返回从指定时间点开始经过的毫秒数。"""
    return max(0, int((time.perf_counter() - started_at) * 1000))


if __name__ == '__main__':
    pass
