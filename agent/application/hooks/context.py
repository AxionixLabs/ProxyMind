# -*- coding: utf-8 -*-

import typing
from dataclasses import dataclass

from agent.domain.hooks import HookEventName
from ..turns.context import TurnContext
from .protocol import build_hook_input


@dataclass(frozen=True, slots=True)
class HookExecutionContext:
    """描述一个 Hook 执行作用域的公共输入上下文。"""

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
        event_payload: dict[str, typing.Any] | None = None,
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


__all__ = ("HookExecutionContext",)


if __name__ == '__main__':
    pass
