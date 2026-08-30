# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from agent.application import HookEventName
from mind_app.runtime.execution import TurnContext
from .models import (
    HookDispatchResult,
    HookEventRequest
)
from .protocol import (
    build_hook_input,
    validate_hook_input
)
from .runtime import (
    HookDispatcher,
    HookStatusPort,
    HookRuntime
)


@dataclass(frozen=True, slots=True)
class HookExecutionContext:
    """描述一个 Hook 执行作用域的公共上下文。"""
    session_id: str
    conversation_id: str
    cwd: str
    model: str
    source: str
    sandbox_mode: str
    permission_mode: str
    agent_id: str
    agent_type: str
    agent_depth: int
    parent_agent_id: str | None = None
    root_session_id: str = ""
    turn_id: str = ""
    transcript_path: str | None = None
    parent_transcript_path: str | None = None
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
            source=turn.source,
            sandbox_mode=turn.permissions.sandbox_mode,
            permission_mode=turn.permissions.approval_policy,
            agent_id=turn.agent.agent_id,
            agent_type=turn.agent.agent_type,
            agent_depth=turn.agent.depth,
            parent_agent_id=turn.agent.parent_agent_id,
            transcript_path=turn.transcript_path or None,
            parent_transcript_path=turn.parent_transcript_path or None,
            session_started=turn.session_started,
            session_start_reason=turn.session_start_reason,
        )

    def payload(
        self,
        event: HookEventName,
        event_payload: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """返回符合事件输入协议的 stdin 对象。"""
        transcript_path = (
            self.parent_transcript_path
            if event == "SubagentStop" and self.agent_depth > 0
            else self.transcript_path
        )
        return build_hook_input(
            event,
            session_id=self.session_id,
            transcript_path=transcript_path,
            cwd=self.cwd,
            model=self.model,
            permission_mode=_permission_mode(self.permission_mode),
            turn_id=self.turn_id,
            agent_id=self.agent_id,
            agent_type=self.agent_type,
            include_agent=self.agent_depth > 0,
            payload=event_payload,
        )


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
        event_payload = self.context.payload(event, payload)
        validate_hook_input(event, event_payload)

        return await self.dispatcher.dispatch(HookEventRequest(
            event=event,
            payload=event_payload,
            match_value=match_value,
            diagnostics=dict(diagnostics or {}),
        ))

    def with_default_status_port(
        self,
        status_port: HookStatusPort
    ) -> "HookExecutionScope":
        """在分发器尚未配置展示端时绑定单轮状态端口。"""
        dispatcher = self.dispatcher.with_default_status_port(status_port)
        if dispatcher is self.dispatcher:
            return self
        return type(self)(context=self.context, dispatcher=dispatcher)

    def require_turn(self, turn: TurnContext) -> None:
        """验证模型轮次属于当前固定作用域。"""
        if HookExecutionContext.from_turn(turn) != self.context:
            raise ValueError("turn does not belong to hook scope")


def _permission_mode(approval_policy: str) -> str:
    """把本地执行权限转换为 Hook 协议的权限模式。"""
    value = str(approval_policy or "").strip()
    if value in {
        "default",
        "acceptEdits",
        "plan",
        "dontAsk",
        "bypassPermissions",
    }:
        return value

    if value == "never":
        return "bypassPermissions"

    return "default"


if __name__ == '__main__':
    pass
