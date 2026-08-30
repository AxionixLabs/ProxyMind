# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    replace
)
from protocol.schema.stream_events import ExecutionEffect
from agent.application import PermissionSettings
from protocol.schema.identifiers import (
    normalize_turn_id,
    short_uid
)

if typing.TYPE_CHECKING:
    from mind_app.approval.permission_grants import PermissionGrantStore

ROOT_AGENT_ID   = "root"
ROOT_AGENT_TYPE = "root"


@dataclass(frozen=True, slots=True)
class AgentContext:
    """描述一次执行中的 Agent 身份和层级关系。"""
    agent_id: str
    agent_type: str
    root_session_id: str
    task_name: str
    task_path: str
    parent_agent_id: str | None = None
    depth: int = 0

    def __post_init__(self) -> None:
        """校验执行主体身份和层级关系。"""
        agent_id        = str(self.agent_id or "").strip()
        agent_type      = str(self.agent_type or "").strip()
        root_session_id = str(self.root_session_id or "").strip()
        task_name       = str(self.task_name or "").strip().casefold()
        task_path       = str(self.task_path or "").strip()
        parent_agent_id = str(self.parent_agent_id or "").strip()

        if not agent_id:
            raise ValueError("agent id is required")
        if not agent_type:
            raise ValueError("agent type is required")
        if not root_session_id:
            raise ValueError("root session id is required")

        _validate_task_name(task_name)

        if (
            isinstance(self.depth, bool)
            or not isinstance(self.depth, int)
            or self.depth < 0
        ):
            raise ValueError("agent depth must be a non-negative integer")

        if self.depth == 0 and parent_agent_id:
            raise ValueError("root agent cannot have a parent")
        if self.depth > 0 and not parent_agent_id:
            raise ValueError("child agent parent id is required")
        if parent_agent_id and parent_agent_id == agent_id:
            raise ValueError("agent cannot be its own parent")
        if self.depth == 0 and (
            agent_id != ROOT_AGENT_ID
            or agent_type != ROOT_AGENT_TYPE
            or task_name != ROOT_AGENT_ID
            or task_path != "/root"
        ):
            raise ValueError("root agent must use the reserved root identity")
        if self.depth > 0 and (
            agent_id == ROOT_AGENT_ID
            or agent_type == ROOT_AGENT_TYPE
        ):
            raise ValueError("child agent cannot use the reserved root identity")

        _validate_task_path(task_path, task_name, self.depth)

        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "agent_type", agent_type)
        object.__setattr__(self, "root_session_id", root_session_id)
        object.__setattr__(self, "task_name", task_name)
        object.__setattr__(self, "task_path", task_path)
        object.__setattr__(self, "parent_agent_id", parent_agent_id or None)

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
            task_name=ROOT_AGENT_ID,
            task_path="/root",
        )

    def child(
        self,
        agent_type: str,
        task_name: str,
        *,
        agent_id: str | None = None
    ) -> "AgentContext":
        """创建继承根会话和父级关系的子执行主体。"""
        normalized_type = str(agent_type or "").strip()
        normalized_name = str(task_name or "").strip().casefold()
        normalized_id   = str(agent_id or "").strip() or short_uid(12)

        if not normalized_type:
            raise ValueError("child agent type is required")

        _validate_task_name(normalized_name)

        return AgentContext(
            agent_id=normalized_id,
            agent_type=normalized_type,
            root_session_id=self.root_session_id,
            task_name=normalized_name,
            task_path=f"{self.task_path}/{normalized_name}",
            parent_agent_id=self.agent_id,
            depth=self.depth + 1,
        )

    def resolve_task_reference(self, reference: str) -> str:
        """把绝对或相对任务引用解析为规范路径。"""
        if not isinstance(reference, str):
            raise TypeError("agent target must be a string")
        normalized = reference.strip()
        if not normalized:
            raise ValueError("agent target is required")

        candidate = (
            normalized
            if normalized.startswith("/")
            else f"{self.task_path}/{normalized}"
        )
        parts = candidate.split("/")
        if candidate == "/root":
            _validate_task_path(candidate, ROOT_AGENT_ID, 0)
            return candidate

        task_name = parts[-1] if parts else ""
        depth = len(parts) - 2
        _validate_task_path(candidate, task_name, depth)
        return candidate


@dataclass(frozen=True, slots=True)
class TurnContext:
    """描述一次模型轮次共享的执行上下文。"""
    agent: AgentContext
    turn_id: str
    cid: str
    sid: str
    source: str
    model: str
    cwd: str
    permissions: PermissionSettings
    permission_grants: "PermissionGrantStore | None" = None
    output_record_path: str = ""
    transcript_path: str = ""
    parent_transcript_path: str = ""
    session_started: bool = False
    session_start_reason: str = ""

    @classmethod
    def create(
        cls,
        *,
        agent: AgentContext,
        cid: str,
        sid: str,
        source: str,
        pref_config: dict[str, typing.Any],
        cwd: str,
        permissions: PermissionSettings,
        permission_grants: "PermissionGrantStore | None" = None,
        output_record_path: str = "",
        transcript_path: str = "",
        parent_transcript_path: str = "",
        turn_id: str | None = None,
        session_started: bool = False,
        session_start_reason: str = ""
    ) -> "TurnContext":
        """从会话与运行配置创建轮次上下文。"""
        normalized_cid     = str(cid or "").strip()
        normalized_sid     = str(sid or "").strip()

        normalized_turn_id = normalize_turn_id(
            str(turn_id or "").strip() or short_uid(12)
        )

        if not normalized_cid or not normalized_sid:
            raise ValueError("cid and sid are required")

        if agent.depth == 0 and agent.root_session_id != normalized_sid:
            raise ValueError("agent root session does not match turn session")

        primary = pref_config.get("primary") if isinstance(pref_config, dict) else None
        model   = str(primary.get("model") or "").strip() if isinstance(primary, dict) else ""

        return cls(
            agent=agent,
            turn_id=normalized_turn_id,
            cid=normalized_cid,
            sid=normalized_sid,
            source=str(source or "").strip(),
            model=model,
            cwd=str(cwd or "").strip(),
            permissions=permissions,
            permission_grants=permission_grants,
            output_record_path=str(output_record_path or "").strip(),
            transcript_path=str(transcript_path or "").strip(),
            parent_transcript_path=str(parent_transcript_path or "").strip(),
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
    effect: ExecutionEffect | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        """复制可变输入，避免调用建立后被外部修改。"""
        object.__setattr__(self, "arguments", dict(self.arguments))
        if self.meta is not None:
            object.__setattr__(self, "meta", dict(self.meta))

    def with_arguments(self, arguments: dict[str, typing.Any]) -> "ToolInvocation":
        """返回替换工具参数后的调用快照。"""
        return replace(self, arguments=dict(arguments))


def _validate_task_name(value: str) -> None:
    """校验任务名称格式。"""
    if not value or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
        for character in value
    ):
        raise ValueError(
            "task name must contain only lowercase letters, digits, and underscores"
        )


def _validate_task_path(value: str, task_name: str, depth: int) -> None:
    """校验任务路径与层级身份的一致性。"""
    parts = value.split("/") if value else []
    if (
        not value.startswith("/")
        or not parts
        or parts[0]
        or parts[-1] != task_name
        or len(parts) != depth + 2
        or parts[1] != "root"
    ):
        raise ValueError("task path does not match task identity")
    for part in parts[1:]:
        _validate_task_name(part)


if __name__ == '__main__':
    pass
