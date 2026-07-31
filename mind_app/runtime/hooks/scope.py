# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_core.hooks import HookEventName
from mind_app.runtime.execution import TurnContext
from .models import (
    HookDispatchResult,
    HookEventRequest
)
from .protocol import validate_hook_input
from .runtime import (
    HookDispatcher,
    HookRuntime
)


@dataclass(frozen=True, slots=True)
class HookExecutionContext:
    """描述一个 Hook 执行作用域的公共上下文。"""
    session_id: str
    conversation_id: str
    cwd: str
    model: str
    mode: str
    source: str
    sandbox_mode: str
    permission_mode: str
    agent_id: str
    agent_type: str
    agent_depth: int
    parent_agent_id: str | None = None
    root_session_id: str = ""
    turn_id: str = ""
    session_started: bool = False
    session_start_reason: str = ""

    @classmethod
    def from_turn(cls, turn: TurnContext) -> "HookExecutionContext":
        """从模型轮次创建 Hook 执行上下文。"""
        return cls(
            session_id=turn.sid,
            root_session_id=turn.agent.root_session_id,
            conversation_id=turn.cid,
            turn_id=turn.turn_id,
            cwd=turn.cwd,
            model=turn.model,
            mode=turn.mode,
            source=turn.source,
            sandbox_mode=turn.permissions.sandbox_mode,
            permission_mode=turn.permissions.approval_policy,
            agent_id=turn.agent.agent_id,
            agent_type=turn.agent.agent_type,
            agent_depth=turn.agent.depth,
            parent_agent_id=turn.agent.parent_agent_id,
            session_started=turn.session_started,
            session_start_reason=turn.session_start_reason,
        )

    def payload(self) -> dict[str, typing.Any]:
        """返回命令 Hook 可见的公共字段。"""
        return {
            "session_id"           : self.session_id,
            "root_session_id"      : self.root_session_id or self.session_id,
            "conversation_id"      : self.conversation_id,
            "turn_id"              : self.turn_id,
            "cwd"                  : self.cwd,
            "model"                : self.model,
            "mode"                 : self.mode,
            "source"               : self.source,
            "sandbox_mode"         : self.sandbox_mode,
            "permission_mode"      : self.permission_mode,
            "agent_id"             : self.agent_id,
            "agent_type"           : self.agent_type,
            "agent_depth"          : self.agent_depth,
            "parent_agent_id"      : self.parent_agent_id,
            "session_started"      : self.session_started,
            "session_start_reason" : self.session_start_reason
        }


@dataclass(frozen=True, slots=True)
class HookExecutionScope:
    """在一个执行作用域内固定 Hook 运行时和公共上下文。"""
    context: HookExecutionContext
    dispatcher: HookDispatcher

    @classmethod
    def empty(cls, context: HookExecutionContext) -> "HookExecutionScope":
        """返回不包含活动 Hook 的执行作用域。"""
        return cls(context=context, dispatcher=HookRuntime.empty())

    def has_matching(
        self,
        event: HookEventName,
        match_value: str = ""
    ) -> bool:
        """判断当前作用域是否存在匹配 Hook。"""
        return self.dispatcher.has_matching(event, match_value)

    async def dispatch(
        self,
        event: HookEventName,
        *,
        payload: dict[str, typing.Any] | None = None,
        match_value: str = "",
        diagnostics: dict[str, typing.Any] | None = None
    ) -> HookDispatchResult:
        """合并公共上下文并分发一次生命周期事件。"""
        event_payload = {
            **dict(payload or {}),
            **self.context.payload(),
            "hook_event_name": event,
        }
        validate_hook_input(event, event_payload)
        return await self.dispatcher.dispatch(HookEventRequest(
            event=event,
            payload=event_payload,
            match_value=match_value,
            diagnostics=dict(diagnostics or {}),
        ))

    def require_turn(self, turn: TurnContext) -> None:
        """验证模型轮次属于当前固定作用域。"""
        if HookExecutionContext.from_turn(turn) != self.context:
            raise ValueError("turn does not belong to hook scope")


if __name__ == '__main__':
    pass
