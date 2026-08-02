# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from dataclasses import dataclass
from types import MappingProxyType
from mind_core.permissions import PermissionSettings
from mind_nova.identifiers import (
    new_cid,
    new_sid
)
from mind_app.history.ids import valid_session_ids
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext
)
from mind_app.runtime.subagents.context import (
    ForkTurns,
    normalize_fork_turns
)


@dataclass(frozen=True, slots=True)
class AgentThreadContext:
    """描述子执行主体在线程生命周期内固定的运行上下文。"""
    agent: AgentContext
    cid: str
    sid: str
    source: str
    cwd: str
    permissions: PermissionSettings
    pref_config: typing.Mapping[str, typing.Any]
    spawn_turn_id: str
    fork_turns: ForkTurns = "all"
    fork_context: tuple[str, ...] = ()
    transcript_path: str = ""
    skills: tuple[typing.Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        """校验线程身份并固定配置快照。"""
        if self.agent.depth == 0:
            raise ValueError("agent thread requires a child context")

        cid = str(self.cid or "").strip()
        sid = str(self.sid or "").strip()

        if not valid_session_ids(cid, sid):
            raise ValueError("agent thread requires valid cid and sid")
        if not isinstance(self.permissions, PermissionSettings):
            raise TypeError("agent thread permissions are required")

        source          = str(self.source or "").strip()
        cwd             = str(self.cwd or "").strip()
        spawn_turn_id   = str(self.spawn_turn_id or "").strip()
        fork_turns      = normalize_fork_turns(self.fork_turns)
        transcript_path = str(self.transcript_path or "").strip()

        if not source or not cwd or not spawn_turn_id:
            raise ValueError("agent thread source, cwd, and spawn turn are required")
        if not isinstance(self.fork_context, (tuple, list)):
            raise TypeError("agent thread fork context must be a sequence")

        pref_config = typing.cast(
            typing.Mapping[str, typing.Any],
            _freeze_config(dict(self.pref_config)),
        )
        skills = tuple(
            typing.cast(
                typing.Mapping[str, str],
                _freeze_config(dict(skill)),
            )
            for skill in self.skills
        )

        object.__setattr__(self, "cid", cid)
        object.__setattr__(self, "sid", sid)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "spawn_turn_id", spawn_turn_id)
        object.__setattr__(self, "fork_turns", fork_turns)
        object.__setattr__(
            self,
            "fork_context",
            tuple(
                value.strip()
                for value in self.fork_context
                if isinstance(value, str) and value.strip()
            ),
        )
        object.__setattr__(self, "transcript_path", transcript_path)
        object.__setattr__(self, "pref_config", pref_config)
        object.__setattr__(self, "skills", skills)

    @classmethod
    def child(
        cls,
        parent: TurnContext,
        agent_type: str,
        task_name: str,
        pref_config: typing.Mapping[str, typing.Any],
        *,
        skills: typing.Iterable[typing.Mapping[str, str]] = (),
        agent_id: str | None = None,
        fork_turns: ForkTurns = "all",
        fork_context: typing.Iterable[str] = (),
        transcript_path_for: typing.Callable[[str], str] | None = None
    ) -> "AgentThreadContext":
        """从父轮次创建独立的子执行线程。"""
        cid = new_cid()
        sid = new_sid(cid)

        agent = parent.agent.child(
            agent_type,
            task_name,
            agent_id=agent_id,
        )
        path_for = transcript_path_for or (lambda _sid: "")

        return cls(
            agent=agent,
            cid=cid,
            sid=sid,
            source="subagent",
            cwd=parent.cwd,
            permissions=parent.permissions,
            pref_config=pref_config,
            spawn_turn_id=parent.turn_id,
            fork_turns=fork_turns,
            fork_context=tuple(fork_context),
            transcript_path=path_for(sid),
            skills=tuple(skills),
        )

    def config_snapshot(self) -> dict[str, typing.Any]:
        """返回可供单轮执行使用的独立配置副本。"""
        return typing.cast(dict[str, typing.Any], _thaw_config(self.pref_config))

    def skills_snapshot(self) -> list[dict[str, str]]:
        """返回可供单轮请求使用的技能描述副本。"""
        return [dict(skill) for skill in self.skills]


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    """描述执行线程中一次已经分配序号的提交。"""
    thread: AgentThreadContext
    submission_id: str
    turn_index: int

    def __post_init__(self) -> None:
        """校验提交标识和轮次序号。"""
        submission_id = str(self.submission_id or "").strip()
        if not submission_id:
            raise ValueError("agent submission id is required")
        if (
            isinstance(self.turn_index, bool)
            or not isinstance(self.turn_index, int)
            or self.turn_index <= 0
        ):
            raise ValueError("agent turn index must be a positive integer")
        object.__setattr__(self, "submission_id", submission_id)

    @property
    def agent(self) -> AgentContext:
        """返回本轮所属的执行主体身份。"""
        return self.thread.agent


def _freeze_config(value: typing.Any) -> typing.Any:
    """递归固定配置值，避免线程生命周期内发生漂移。"""
    if isinstance(value, typing.Mapping):
        return MappingProxyType({
            key: _freeze_config(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_config(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(_freeze_config(item) for item in value)

    return copy.deepcopy(value)


def _thaw_config(value: typing.Any) -> typing.Any:
    """递归复制线程配置为单轮可用的普通容器。"""
    if isinstance(value, typing.Mapping):
        return {
            key: _thaw_config(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw_config(item) for item in value]

    return copy.deepcopy(value)


if __name__ == '__main__':
    pass
