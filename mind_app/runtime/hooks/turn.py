# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import HookDecision
from .scope import HookExecutionScope


class PromptHookBlockedError(RuntimeError):
    """表示用户输入被前置生命周期 Hook 阻止。"""


class TurnHookEvents:
    """构建并聚合模型轮次生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def session_start(self) -> None:
        """在新会话的首个模型轮次分发启动事件。"""
        context = self.scope.context
        if not context.session_started:
            return None

        reason = context.session_start_reason
        if not self.scope.has_matching("SessionStart", reason):
            return None

        await self.scope.dispatch(
            "SessionStart",
            payload={"reason": reason},
            match_value=reason,
            diagnostics={"session_start_reason": reason},
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
        reasons: list[str] = []

        for record in dispatched.records:
            if not record.ok:
                if record.blocks_event:
                    blocked_keys.append(record.hook_key)
                    reasons.append(_bounded_reason(f"hook failed: {record.error}"))
                continue

            if record.output.get("continue") is False:
                blocked_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    str(record.output.get("reason") or "prompt denied by hook")
                ))

        if blocked_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(blocked_keys),
            )
        return HookDecision.allow()

    async def begin(self, prompt: str) -> None:
        """按顺序执行模型轮次开始前的生命周期事件。"""
        await self.session_start()
        decision = await self.user_prompt_submit(prompt)
        if not decision.allowed:
            raise PromptHookBlockedError(
                decision.reason or "prompt denied by hook"
            )

    async def stop(
        self,
        *,
        outcome: str,
        error: str | None = None,
        usage: dict[str, typing.Any] | None = None,
    ) -> None:
        """在模型轮次结束前分发不可阻断的停止事件。"""
        if not self.scope.has_matching("Stop"):
            return None

        await self.scope.dispatch(
            "Stop",
            payload={
                "outcome": str(outcome or "incomplete"),
                "error": str(error or ""),
                "usage": dict(usage or {}),
            },
            diagnostics={"outcome": str(outcome or "incomplete")},
        )


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回适合主流程错误信息的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
