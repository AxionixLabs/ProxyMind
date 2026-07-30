# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .scope import HookExecutionScope


class SubagentHookEvents:
    """构建本地子执行主体的生命周期事件。"""

    def __init__(self, scope: HookExecutionScope) -> None:
        self.scope = scope

    async def start(self, task: str) -> None:
        """在子执行主体开始模型轮次前分发通知事件。"""
        agent_type = self.scope.context.agent_type
        if not self.scope.has_matching("SubagentStart", agent_type):
            return None

        await self.scope.dispatch(
            "SubagentStart",
            payload={"task": str(task)},
            match_value=agent_type,
            diagnostics={"agent_type": agent_type},
        )

    async def stop(
        self,
        *,
        outcome: str,
        error: str = "",
        usage: dict[str, typing.Any] | None = None
    ) -> None:
        """在子执行主体结束模型轮次前分发通知事件。"""
        agent_type = self.scope.context.agent_type
        if not self.scope.has_matching("SubagentStop", agent_type):
            return None

        normalized_outcome = str(outcome or "incomplete")

        await self.scope.dispatch(
            "SubagentStop",
            payload={
                "outcome": normalized_outcome,
                "error": str(error or ""),
                "usage": dict(usage or {}),
            },
            match_value=agent_type,
            diagnostics={
                "agent_type": agent_type,
                "outcome": normalized_outcome,
            },
        )


if __name__ == '__main__':
    pass
