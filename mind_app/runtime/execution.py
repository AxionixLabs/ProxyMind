# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    replace
)
from mind_core.permissions import PermissionSettings
from mind_nova.identifiers import short_uid
from mind_nova.modes import RunMode

ROOT_AGENT_ID   = "root"
ROOT_AGENT_TYPE = "root"


@dataclass(frozen=True, slots=True)
class AgentContext:
    """描述一次执行中的 Agent 身份和层级关系。"""
    agent_id: str
    agent_type: str
    root_session_id: str
    parent_agent_id: str | None = None
    depth: int = 0

    @classmethod
    def root(cls, session_id: str) -> "AgentContext":
        """为根执行主体创建稳定身份。"""
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("root session id is required")
        return cls(
            agent_id=ROOT_AGENT_ID,
            agent_type=ROOT_AGENT_TYPE,
            root_session_id=normalized_session_id,
        )


@dataclass(frozen=True, slots=True)
class TurnContext:
    """描述一次模型轮次共享的执行上下文。"""
    agent: AgentContext
    turn_id: str
    cid: str
    sid: str
    mode: RunMode
    source: str
    model: str
    cwd: str
    permissions: PermissionSettings
    session_started: bool = False
    session_start_reason: str = ""

    @classmethod
    def create(
        cls,
        *,
        agent: AgentContext,
        cid: str,
        sid: str,
        mode: RunMode,
        source: str,
        pref_config: dict[str, typing.Any],
        cwd: str,
        permissions: PermissionSettings,
        turn_id: str | None = None,
        session_started: bool = False,
        session_start_reason: str = ""
    ) -> "TurnContext":
        """从会话与运行配置创建轮次上下文。"""
        normalized_cid     = str(cid or "").strip()
        normalized_sid     = str(sid or "").strip()
        normalized_turn_id = str(turn_id or "").strip() or short_uid(12)

        if not normalized_cid or not normalized_sid:
            raise ValueError("cid and sid are required")
        if agent.root_session_id != normalized_sid:
            raise ValueError("agent root session does not match turn session")

        primary = pref_config.get("primary") if isinstance(pref_config, dict) else None
        model   = str(primary.get("model") or "").strip() if isinstance(primary, dict) else ""

        return cls(
            agent=agent,
            turn_id=normalized_turn_id,
            cid=normalized_cid,
            sid=normalized_sid,
            mode=mode,
            source=str(source or "").strip(),
            model=model,
            cwd=str(cwd or "").strip(),
            permissions=permissions,
            session_started=bool(session_started),
            session_start_reason=(
                str(session_start_reason or "").strip()
                if session_started
                else ""
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """描述一次工具调用及其所属模型轮次。"""
    turn: TurnContext
    call_id: str
    name: str
    arguments: dict[str, typing.Any]
    meta: dict[str, typing.Any] | None = None
    execution: dict[str, typing.Any] | None = None

    def __post_init__(self) -> None:
        """复制可变输入，避免调用建立后被外部修改。"""
        object.__setattr__(self, "arguments", dict(self.arguments))
        if self.meta is not None:
            object.__setattr__(self, "meta", dict(self.meta))
        if self.execution is not None:
            object.__setattr__(self, "execution", dict(self.execution))

    def with_arguments(self, arguments: dict[str, typing.Any]) -> "ToolInvocation":
        """返回替换工具参数后的调用快照。"""
        return replace(self, arguments=dict(arguments))


if __name__ == '__main__':
    pass
