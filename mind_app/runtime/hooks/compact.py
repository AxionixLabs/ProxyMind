# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_core.hooks import (
    CompactResultSource,
    CompactTriggerReason,
    CompactTriggerSource
)
from .models import HookDecision
from .scope import HookExecutionScope


class CompactHookBlockedError(RuntimeError):
    """表示上下文压缩被前置生命周期 Hook 阻止。"""


class CompactHookEvents:
    """构建并聚合上下文压缩生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def pre_compact(
        self,
        trigger: CompactTriggerReason,
        *,
        trigger_source: CompactTriggerSource = "client"
    ) -> HookDecision:
        """执行压缩前事件并聚合是否继续操作。"""
        if not self.scope.has_matching("PreCompact", trigger):
            return HookDecision.allow()

        dispatched = await self.scope.dispatch(
            "PreCompact",
            payload={
                "trigger": trigger,
                "trigger_source": trigger_source,
            },
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

            if not record.effect.continue_execution:
                blocked_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    record.effect.reason or "compaction denied by hook"
                ))

        if blocked_keys:
            return HookDecision(
                allowed=False,
                reason="; ".join(reason for reason in reasons if reason),
                hook_keys=tuple(blocked_keys),
            )
        return HookDecision.allow()

    async def begin(
        self,
        trigger: CompactTriggerReason,
        *,
        trigger_source: CompactTriggerSource = "client"
    ) -> None:
        """执行上下文压缩开始前的生命周期事件。"""
        decision = await self.pre_compact(
            trigger,
            trigger_source=trigger_source,
        )
        if not decision.allowed:
            raise CompactHookBlockedError(
                decision.reason or "compaction denied by hook"
            )

    async def post_compact(
        self,
        *,
        trigger: CompactTriggerReason,
        trigger_source: CompactTriggerSource,
        result_source: CompactResultSource,
        outcome: str,
        message: str = "",
        summary: str = "",
        transcript_path: str = "",
        before_items: int | None = None,
        after_items: int | None = None
    ) -> HookDecision:
        """执行压缩后事件并聚合下一步执行影响。"""
        if not self.scope.has_matching("PostCompact", trigger):
            return HookDecision.allow()

        dispatched = await self.scope.dispatch(
            "PostCompact",
            payload={
                "trigger": trigger,
                "trigger_source": trigger_source,
                "result_source": result_source,
                "outcome": outcome,
                "message": message,
                "summary": summary,
                "transcript_path": transcript_path,
                "before_items": before_items,
                "after_items": after_items,
            },
            match_value=trigger,
            diagnostics={
                "compact_trigger": trigger,
                "outcome": outcome,
            },
        )

        blocked_keys: list[str]    = []
        reasons: list[str]         = []
        contexts: list[str]        = []
        system_messages: list[str] = []

        for record in dispatched.records:
            if not record.ok:
                continue

            effect = record.effect

            contexts.extend(effect.additional_context)
            if effect.system_message:
                system_messages.append(effect.system_message)

            if not effect.continue_execution:
                blocked_keys.append(record.hook_key)
                reasons.append(_bounded_reason(
                    effect.reason
                    or "post-compaction continuation denied by hook"
                ))

        return HookDecision(
            allowed=not blocked_keys,
            reason="; ".join(reason for reason in reasons if reason),
            hook_keys=tuple(blocked_keys),
            additional_context=tuple(contexts),
            system_message="\n\n".join(system_messages),
        )


def _bounded_reason(value: str, limit: int = 2000) -> str:
    """返回适合压缩结果的有界 Hook 原因。"""
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


if __name__ == '__main__':
    pass
