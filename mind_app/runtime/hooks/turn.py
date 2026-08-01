# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import (
    HookDecision,
    StopHookDecision,
    TurnStartResult
)
from .scope import HookExecutionScope


class PromptHookBlockedError(RuntimeError):
    """表示用户输入被前置生命周期 Hook 阻止。"""


class TurnHookEvents:
    """构建并聚合模型轮次生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def session_start(self) -> HookDecision:
        """在新会话的首个模型轮次分发启动事件。"""
        context = self.scope.context
        if not context.session_started:
            return HookDecision.allow()

        reason = context.session_start_reason
        source = _session_start_source(reason)

        if not self.scope.has_matching("SessionStart", source):
            return HookDecision.allow()

        dispatched = await self.scope.dispatch(
            "SessionStart",
            payload={"source": source},
            match_value=source,
            diagnostics={"session_start_reason": reason},
        )

        contexts: list[str]        = []
        system_messages: list[str] = []

        for record in dispatched.records:
            if not record.ok:
                continue
            contexts.extend(record.effect.additional_context)
            if record.effect.system_message:
                system_messages.append(record.effect.system_message)

        return HookDecision(
            allowed=True,
            additional_context=tuple(contexts),
            system_message="\n\n".join(system_messages),
        )

    async def user_prompt_submit(self, prompt: str) -> HookDecision:
        """分发用户输入事件并聚合是否继续模型轮次。"""
        if not self.scope.has_matching("UserPromptSubmit"):
            return HookDecision.allow()

        dispatched = await self.scope.dispatch(
            "UserPromptSubmit",
            payload={"prompt": str(prompt)},
        )

        updated_input: dict[str, typing.Any] | None = None

        blocked_keys: list[str]    = []
        reasons: list[str]         = []
        contexts: list[str]        = []
        system_messages: list[str] = []

        for record in dispatched.records:
            if not record.ok:
                if record.blocks_event:
                    blocked_keys.append(record.hook_key)
                    reasons.append(_bounded_reason(f"hook failed: {record.error}"))
                continue

            effect = record.effect

            if not effect.continue_execution:
                blocked_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    effect.reason or "prompt denied by hook"
                ))
                continue

            if effect.updated_input is not None:
                if updated_input is None:
                    updated_input = {}
                updated_input.update(effect.updated_input)

            contexts.extend(effect.additional_context)

            if effect.system_message:
                system_messages.append(effect.system_message)

        if blocked_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(blocked_keys),
            )

        return HookDecision(
            allowed=True,
            updated_input=updated_input,
            additional_context=tuple(contexts),
            system_message="\n\n".join(system_messages),
        )

    async def begin(self, prompt: str) -> TurnStartResult:
        """按顺序执行模型轮次开始前的生命周期事件。"""
        session_decision = await self.session_start()

        decision = await self.user_prompt_submit(prompt)
        if not decision.allowed:
            raise PromptHookBlockedError(
                decision.reason or "prompt denied by hook"
            )

        message = _updated_prompt(prompt, decision.updated_input)

        return TurnStartResult(
            message=message,
            additional_context=(
                *session_decision.additional_context,
                *decision.additional_context,
            ),
            system_message="\n\n".join(
                text
                for text in (
                    session_decision.system_message,
                    decision.system_message,
                )
                if text
            ),
        )

    async def stop(
        self,
        *,
        outcome: str,
        error: str | None = None,
        usage: dict[str, typing.Any] | None = None,
        last_assistant_message: str = "",
        continuation_count: int = 0
    ) -> StopHookDecision:
        """在模型轮次结束前分发停止事件并聚合续跑决定。"""
        if not self.scope.has_matching("Stop"):
            return StopHookDecision.stop()

        dispatched = await self.scope.dispatch(
            "Stop",
            payload={
                "stop_hook_active": continuation_count > 0,
                "last_assistant_message": (
                    str(last_assistant_message)
                    if last_assistant_message
                    else None
                ),
            },
            diagnostics={
                "outcome": str(outcome or "incomplete"),
                "hook_error": str(error or ""),
                "usage": dict(usage or {}),
                "continuation_count": continuation_count,
            },
        )

        continuations = tuple(
            record
            for record in dispatched.records
            if record.ok and record.effect.continuation_prompt
        )
        if not continuations:
            return StopHookDecision.stop()

        contexts: list[str]        = []
        system_messages: list[str] = []

        for record in continuations:
            contexts.extend(record.effect.additional_context)
            if record.effect.system_message:
                system_messages.append(record.effect.system_message)

        prompt = "\n\n".join(
            _bounded_reason(record.effect.continuation_prompt, limit=6000)
            for record in continuations
            if record.effect.continuation_prompt
        )

        return StopHookDecision(
            should_continue=True,
            continuation_prompt=prompt,
            reason="; ".join(
                _bounded_reason(record.effect.reason)
                for record in continuations
                if record.effect.reason
            ),
            hook_keys=tuple(record.hook_key for record in continuations),
            additional_context=tuple(contexts),
            system_message="\n\n".join(system_messages),
        )


def _session_start_source(reason: str) -> str:
    """把本地会话边界原因转换为 Hook 启动来源。"""
    normalized = str(reason or "").strip().lower()
    if "compact" in normalized:
        return "compact"
    if "resume" in normalized or normalized in {"bound", "external"}:
        return "resume"
    if normalized in {"", "initial", "startup", "calling", "subagent"}:
        return "startup"

    return "clear"


def _updated_prompt(
    prompt: str,
    updated_input: dict[str, typing.Any] | None
) -> str:
    """返回 Hook 改写后的用户提示词。"""
    if not isinstance(updated_input, dict) or "prompt" not in updated_input:
        return str(prompt)

    value = updated_input.get("prompt")
    return value if isinstance(value, str) else str(prompt)


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回适合主流程错误信息的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
