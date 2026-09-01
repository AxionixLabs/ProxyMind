# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.hooks.models import (
    SubagentStartResult,
    SubagentStopDecision,
)
from agent.ports import HookExecutionScopePort

__all__ = ("SubagentHookEvents",)

_MAX_CONTEXT_CHARS = 12000


class SubagentHookEvents:
    """构建本地子执行主体的生命周期事件。"""

    def __init__(self, scope: HookExecutionScopePort) -> None:
        self.scope = scope

    async def start(self, task: str) -> SubagentStartResult:
        """在子执行主体首次运行前分发事件并聚合上下文。"""
        agent_type = self.scope.context.agent_type
        if not self.scope.has_matching("SubagentStart", agent_type):
            return SubagentStartResult()

        result = await self.scope.dispatch(
            "SubagentStart",
            match_value=agent_type,
            diagnostics={
                "agent_type": agent_type,
                "task": str(task),
            },
        )

        contexts = tuple(
            context
            for record in result.records
            if record.ok
            for context in record.effect.additional_context
            if context
        )
        return SubagentStartResult(
            additional_context=_bounded_parts(
                contexts,
                limit=_MAX_CONTEXT_CHARS,
            ),
        )

    async def stop(
        self,
        *,
        outcome: str,
        error: str = "",
        usage: dict[str, typing.Any] | None = None,
        last_assistant_message: str = "",
        continuation_count: int = 0,
    ) -> SubagentStopDecision:
        """在子执行主体结束模型轮次前分发事件并聚合继续决定。"""
        agent_type = self.scope.context.agent_type
        if not self.scope.has_matching("SubagentStop", agent_type):
            return SubagentStopDecision.stop()

        normalized_outcome = str(outcome or "incomplete")

        result = await self.scope.dispatch(
            "SubagentStop",
            payload={
                "agent_transcript_path": self.scope.context.transcript_path,
                "stop_hook_active": continuation_count > 0,
                "last_assistant_message": (
                    str(last_assistant_message)
                    if last_assistant_message
                    else None
                ),
            },
            match_value=agent_type,
            diagnostics={
                "agent_type": agent_type,
                "outcome": normalized_outcome,
                "hook_error": str(error or ""),
                "usage": dict(usage or {}),
                "continuation_count": continuation_count,
            },
        )

        vetoes = tuple(
            record.hook_key
            for record in result.records
            if record.ok and not record.effect.continue_execution
        )
        if vetoes:
            return SubagentStopDecision(
                should_continue=False,
                hook_keys=vetoes,
            )

        continuations = tuple(
            record
            for record in result.records
            if record.ok and record.effect.continuation_prompt
        )
        if not continuations:
            return SubagentStopDecision.stop()

        contexts = _bounded_parts(
            tuple(
                context
                for record in continuations
                for context in record.effect.additional_context
            ),
            limit=_MAX_CONTEXT_CHARS,
        )

        return SubagentStopDecision(
            should_continue=True,
            continuation_prompt="\n\n".join(
                record.effect.continuation_prompt
                for record in continuations
            ),
            reason="; ".join(
                record.effect.reason
                for record in continuations
                if record.effect.reason
            ),
            hook_keys=tuple(record.hook_key for record in continuations),
            additional_context=contexts,
        )


def _bounded_parts(
    values: tuple[str, ...],
    *,
    limit: int,
) -> tuple[str, ...]:
    """按原始顺序截取不超过总长度限制的文本集合。"""
    remaining = limit
    bounded: list[str] = []

    for value in values:
        if remaining <= 0:
            break
        text = value[:remaining]
        if text:
            bounded.append(text)
            remaining -= len(text)

    return tuple(bounded)


if __name__ == '__main__':
    pass
