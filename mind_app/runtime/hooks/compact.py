# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .models import HookDecision
from .scope import HookExecutionScope


class CompactHookBlockedError(RuntimeError):
    """表示上下文压缩被前置生命周期 Hook 阻止。"""


class CompactHookEvents:
    """构建并聚合上下文压缩生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def pre_compact(self, trigger: str) -> HookDecision:
        """执行压缩前事件并聚合是否继续操作。"""
        if not self.scope.has_matching("PreCompact", trigger):
            return HookDecision.allow()

        dispatched = await self.scope.dispatch(
            "PreCompact",
            payload={"trigger": trigger},
            match_value=trigger,
            diagnostics={"compact_trigger": trigger},
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
                reasons.append(_bounded_reason(str(
                    record.output.get("reason") or "compaction denied by hook"
                )))

        if blocked_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(blocked_keys),
            )
        return HookDecision.allow()

    async def begin(self, trigger: str) -> None:
        """执行上下文压缩开始前的生命周期事件。"""
        decision = await self.pre_compact(trigger)
        if not decision.allowed:
            raise CompactHookBlockedError(
                decision.reason or "compaction denied by hook"
            )

    async def post_compact(
        self,
        *,
        trigger: str,
        outcome: str,
        message: str = "",
        before_items: int | None = None,
        after_items: int | None = None,
    ) -> None:
        """执行上下文压缩结束后的不可阻断事件。"""
        if not self.scope.has_matching("PostCompact", trigger):
            return None

        await self.scope.dispatch(
            "PostCompact",
            payload={
                "trigger": trigger,
                "outcome": outcome,
                "message": message,
                "before_items": before_items,
                "after_items": after_items,
            },
            match_value=trigger,
            diagnostics={
                "compact_trigger": trigger,
                "outcome": outcome,
            },
        )


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回适合压缩结果的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
