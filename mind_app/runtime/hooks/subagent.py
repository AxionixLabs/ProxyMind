# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import (
    HookExecutionRecord,
    SubagentStartResult,
    SubagentStopDecision
)
from .scope import HookExecutionScope

_MAX_CONTEXT_CHARS = 12000
_MAX_REASON_CHARS  = 6000


class SubagentHookEvents:
    """构建本地子执行主体的生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def start(self, task: str) -> SubagentStartResult:
        """在子执行主体首次运行前分发事件并聚合上下文。"""
        agent_type = self.scope.context.agent_type
        if not self.scope.has_matching("SubagentStart", agent_type):
            return SubagentStartResult()

        result = await self.scope.dispatch(
            "SubagentStart",
            payload={"task": str(task)},
            match_value=agent_type,
            diagnostics={"agent_type": agent_type},
        )

        contexts = tuple(
            context
            for record in result.records
            if record.ok
            for context in [_record_text(record, "additional_context")]
            if context
        )

        return SubagentStartResult(
            additional_context=_bounded_parts(
                contexts,
                limit=_MAX_CONTEXT_CHARS,
            )
        )

    async def stop(
        self,
        *,
        outcome: str,
        error: str = "",
        usage: dict[str, typing.Any] | None = None,
        last_assistant_message: str = "",
        continuation_count: int = 0
    ) -> SubagentStopDecision:
        """在子执行主体结束模型轮次前分发事件并聚合继续决定。"""
        agent_type = self.scope.context.agent_type
        if not self.scope.has_matching("SubagentStop", agent_type):
            return SubagentStopDecision.stop()

        normalized_outcome = str(outcome or "incomplete")

        result = await self.scope.dispatch(
            "SubagentStop",
            payload={
                "outcome": normalized_outcome,
                "error": str(error or ""),
                "usage": dict(usage or {}),
                "agent_transcript_path": None,
                "stop_hook_active": continuation_count > 0,
                "last_assistant_message": str(
                    last_assistant_message or ""
                ),
                "continuation_count": continuation_count,
            },
            match_value=agent_type,
            diagnostics={
                "agent_type": agent_type,
                "outcome": normalized_outcome,
            },
        )

        vetoes = tuple(
            record.hook_key
            for record in result.records
            if record.ok and record.output.get("continue") is False
        )
        if vetoes:
            return SubagentStopDecision(
                should_continue=False,
                hook_keys=vetoes,
            )

        continuations = tuple(
            record
            for record in result.records
            if record.ok and record.output.get("decision") == "block"
        )
        if not continuations:
            return SubagentStopDecision.stop()

        reasons = _bounded_parts(
            tuple(_record_text(record, "reason") for record in continuations),
            limit=_MAX_REASON_CHARS,
        )

        return SubagentStopDecision(
            should_continue=True,
            reason="\n\n".join(reasons),
            hook_keys=tuple(record.hook_key for record in continuations),
        )


def _record_text(record: HookExecutionRecord, key: str) -> str:
    """返回单个 Hook 输出中的非空有界文本。"""
    value = record.output.get(key, "")
    return str(value).strip() if isinstance(value, str) else ""


def _bounded_parts(
    values: tuple[str, ...],
    *,
    limit: int
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
