# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.hooks.models import (
    HookDecision,
    StopHookDecision,
    TurnStartResult
)
from .scope import HookExecutionScope

SessionStartSource = typing.Literal[
    "startup",
    "resume",
    "clear",
    "compact",
]


class PromptHookBlockedError(RuntimeError):
    """表示用户输入被前置生命周期 Hook 阻止。"""

    def __init__(
        self,
        reason: str,
        *,
        additional_context: typing.Iterable[str] = ()
    ) -> None:
        super().__init__(reason)

        self.additional_context = tuple(
            text
            for value in additional_context
            for text in [str(value or "").strip()]
            if text
        )


class TurnHookEvents:
    """构建并聚合模型轮次生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def session_start(
        self,
        *,
        source: SessionStartSource | None = None
    ) -> HookDecision:
        """在会话启动或压缩边界分发启动事件。"""
        context = self.scope.context
        if source is None and context.agent_depth > 0:
            return HookDecision.allow()
        if source is None and not context.session_started:
            return HookDecision.allow()

        reason = context.session_start_reason if source is None else source

        hook_source = source or _session_start_source(reason)

        if not self.scope.has_matching("SessionStart", hook_source):
            return HookDecision.allow()

        dispatched = await self.scope.dispatch(
            "SessionStart",
            payload={"source": hook_source},
            match_value=hook_source,
            diagnostics={"session_start_reason": reason},
        )

        contexts: list[str] = []

        for record in dispatched.records:
            if record.ok:
                contexts.extend(record.effect.additional_context)

        blocked = tuple(
            record
            for record in dispatched.records
            if record.ok and not record.effect.continue_execution
        )

        if blocked:
            return HookDecision(
                allowed=False,
                reason="; ".join(
                    _bounded_reason(
                        record.effect.reason
                        or "session start denied by hook"
                    )
                    for record in blocked
                ),
                hook_keys=tuple(record.hook_key for record in blocked),
                additional_context=tuple(contexts),
            )

        return HookDecision(
            allowed=True,
            additional_context=tuple(contexts),
        )

    async def user_prompt_submit(self, prompt: str) -> HookDecision:
        """分发用户输入事件并聚合是否继续模型轮次。"""
        if not self.scope.has_matching("UserPromptSubmit"):
            return HookDecision.allow()

        dispatched = await self.scope.dispatch(
            "UserPromptSubmit",
            payload={"prompt": str(prompt)},
        )

        blocked_keys: list[str] = []
        reasons: list[str]      = []
        contexts: list[str]     = []

        for record in dispatched.records:
            if not record.ok:
                continue

            effect = record.effect
            contexts.extend(effect.additional_context)

            if not effect.continue_execution:
                blocked_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    effect.reason or "prompt denied by hook"
                ))
                continue

        if blocked_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(blocked_keys),
                additional_context=tuple(contexts),
            )

        return HookDecision(
            allowed=True,
            additional_context=tuple(contexts),
        )

    async def begin(self, prompt: str) -> TurnStartResult:
        """按顺序执行模型轮次开始前的生命周期事件。"""
        session_decision = await self.session_start()

        if not session_decision.allowed:
            raise PromptHookBlockedError(
                session_decision.reason or "session start denied by hook",
                additional_context=session_decision.additional_context,
            )

        decision = await self.user_prompt_submit(prompt)
        if not decision.allowed:
            raise PromptHookBlockedError(
                decision.reason or "prompt denied by hook",
                additional_context=(
                    *session_decision.additional_context,
                    *decision.additional_context,
                ),
            )

        return TurnStartResult(
            message=prompt,
            additional_context=(
                *session_decision.additional_context,
                *decision.additional_context,
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
        if self.scope.context.agent_depth > 0:
            return StopHookDecision.stop()
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

        successful = tuple(
            record
            for record in dispatched.records
            if record.ok
        )
        if any(
            not record.effect.continue_execution
            for record in successful
        ):
            return StopHookDecision.stop()

        continuations = tuple(
            record
            for record in successful
            if record.effect.continuation_prompt
        )
        if not continuations:
            return StopHookDecision.stop()

        contexts: list[str] = []

        for record in continuations:
            contexts.extend(record.effect.additional_context)

        prompt = "\n\n".join(
            record.effect.continuation_prompt
            for record in continuations
            if record.effect.continuation_prompt
        )

        return StopHookDecision(
            should_continue=True,
            continuation_prompt=prompt,
            reason="; ".join(
                record.effect.reason
                for record in continuations
                if record.effect.reason
            ),
            hook_keys=tuple(record.hook_key for record in continuations),
            additional_context=tuple(contexts),
        )


def _session_start_source(reason: str) -> str:
    """把本地会话边界原因转换为 Hook 启动来源。"""
    normalized = str(reason or "").strip().lower()
    if "compact" in normalized:
        return "compact"
    if "resume" in normalized or normalized in {"bound", "external"}:
        return "resume"
    if normalized in {"", "initial", "startup", "calling"}:
        return "startup"

    return "clear"


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回适合主流程错误信息的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
