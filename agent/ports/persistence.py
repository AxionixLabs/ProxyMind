# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Callable,
    Mapping,
)
from dataclasses import dataclass

from agent.domain import (
    RecoveryAction,
    RunStatus,
)
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand,
)
from agent.protocol.json_value import ThawedJsonValue


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """描述可脱离具体数据库实现读取的 Run 恢复快照。"""

    command: SubmitTurnCommand
    status: RunStatus
    sequence: int
    snapshot_version: int
    effect_id: str
    effect_status: str
    recovery_action: RecoveryAction | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class RunRecoveryDetail:
    """描述一项阻止新 Run 派发的恢复快照摘要。"""

    run_id: str
    session_id: str
    status: str
    sequence: int
    effect_id: str
    effect_status: str
    action: str
    updated_at: str


RecoveryResolution: typing.TypeAlias = typing.Literal[
    "committed",
    "failed",
    "not_executed",
]


@dataclass(frozen=True, slots=True)
class RunRecoveryResolutionRecord:
    """描述一项已经由权威来源确认的 Run 恢复决议。"""

    run_id: str
    request_id: str
    resolution: RecoveryResolution
    result_payload: Mapping[str, ThawedJsonValue] | None
    error: str
    resolved_at: str


@dataclass(frozen=True, slots=True)
class RemoteTurnBinding:
    """描述本地 Run 对应的服务端 Session/Turn 坐标。"""

    cid: str
    sid: str
    turn_id: str


def remote_turn_binding(
    command: SubmitTurnCommand,
) -> RemoteTurnBinding | None:
    """从已持久化命令中读取严格的远端轮次绑定。"""
    raw_binding = command.trace_context.get("remote_turn")
    if raw_binding is None:
        return None
    if not isinstance(raw_binding, Mapping):
        raise RunPersistenceConflict("remote turn binding must be an object")
    values: dict[str, str] = {}
    for field_name in ("cid", "sid", "turn_id"):
        value = raw_binding.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise RunPersistenceConflict(
                f"remote turn binding requires {field_name}"
            )
        values[field_name] = value.strip()
    return RemoteTurnBinding(
        cid=values["cid"],
        sid=values["sid"],
        turn_id=values["turn_id"],
    )


@dataclass(frozen=True, slots=True)
class RunFact:
    """描述一个 Run 可独立读取的最终消息、工具或证据事实。"""

    run_id: str
    kind: str
    position: int
    payload: Mapping[str, typing.Any]


class RunPersistenceConflict(ValueError):
    """表示持久化身份或事件序列与已有 Run 事实冲突。"""


class RunRecoveryRequired(RuntimeError):
    """表示 Session 存在必须显式恢复或核对的未终结 Run。"""

    def __init__(self, snapshots: tuple[RunSnapshot, ...]) -> None:
        """保存阻止新派发的权威恢复快照。"""
        self.snapshots = snapshots
        self.details = tuple(
            RunRecoveryDetail(
                run_id=item.command.run_id,
                session_id=item.command.session_id,
                status=item.status.value,
                sequence=item.sequence,
                effect_id=item.effect_id,
                effect_status=item.effect_status,
                action=(
                    item.recovery_action.value
                    if item.recovery_action is not None
                    else ""
                ),
                updated_at=item.updated_at,
            )
            for item in snapshots
        )
        actions = ", ".join(
            f"{item.command.run_id}:{item.recovery_action.value}"
            for item in snapshots
            if item.recovery_action is not None
        )
        super().__init__(f"session recovery is required: {actions}")


class RunPersistence(typing.Protocol):
    """持久化 Run 事件、快照与 outbox，并提供重启恢复读取。"""

    async def append_event(
        self,
        command: SubmitTurnCommand,
        event: RunEvent,
    ) -> None:
        """在同一事务中追加事件并推进快照与 outbox。"""
        ...

    async def find_run(
        self,
        command: SubmitTurnCommand,
    ) -> RunSnapshot | None:
        """按 Run 或幂等身份读取并校验已有快照。"""
        ...

    async def recover_session(
        self,
        session_id: str,
    ) -> tuple[RunSnapshot, ...]:
        """读取 Session 中全部未终结 Run 及其恢复动作。"""
        ...

    async def resolve_recovery(
        self,
        run_id: str,
        *,
        request_id: str,
        resolution: RecoveryResolution,
        result_payload: Mapping[str, ThawedJsonValue] | None = None,
        error: str = "",
    ) -> RunRecoveryResolutionRecord:
        """幂等记录权威恢复决议并解除对应 Run 的恢复门禁。"""
        ...

    async def load_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[RunEvent, ...]:
        """从指定本地事件游标之后读取 Run 事实。"""
        ...

    async def load_facts(
        self,
        run_id: str,
        *,
        kind: str | None = None,
    ) -> tuple[RunFact, ...]:
        """读取最终消息、工具结果、审批决定和证据引用。"""
        ...


EffectReplay = typing.Literal["safe", "manual"]


class EffectIntent(typing.Protocol):
    """约束外部效果必须携带稳定身份、SHA-256 指纹和重放策略。"""

    effect_id: str
    fingerprint: str
    replay: EffectReplay


@dataclass(frozen=True, slots=True)
class EffectJournalDecision:
    """描述本地效果是否执行、复用或等待核对。"""

    action: typing.Literal["execute", "reuse", "reconcile"]
    result_payload: dict[str, typing.Any] | None = None


class LocalEffectReconciliationRequired(RuntimeError):
    """表示本地效果结果不确定，必须先核对再继续。"""

    def __init__(self, effect_id: str) -> None:
        """保存需要核对的效果标识。"""
        self.effect_id = str(effect_id or "").strip()
        super().__init__(f"local effect requires reconciliation: {self.effect_id}")


class EffectJournalPersistenceError(RuntimeError):
    """表示本地效果账本无法完成持久化操作。"""


class EffectJournal(typing.Protocol):
    """持久化外部效果执行权、确定结果和人工核对状态。"""

    async def inspect(self, effect: EffectIntent) -> EffectJournalDecision:
        """读取效果状态但不取得执行权。"""
        ...

    async def begin(self, effect: EffectIntent) -> EffectJournalDecision:
        """原子取得执行权或返回已有决定。"""
        ...

    async def commit(
        self,
        effect: EffectIntent,
        result_payload: dict[str, typing.Any],
    ) -> None:
        """提交效果的确定结果。"""
        ...

    async def mark_unknown(
        self,
        effect: EffectIntent,
        error: BaseException,
        *,
        result_payload: dict[str, typing.Any] | None = None,
    ) -> None:
        """把不确定结果转入人工核对。"""
        ...

    async def reconciliation_result(
        self,
        effect_id: str,
    ) -> dict[str, typing.Any] | None:
        """读取可用于核对的确定候选结果。"""
        ...

    async def mark_reconciled(self, effect_id: str) -> None:
        """提交已经由外部权威确认的效果结果。"""
        ...


EffectJournalFactory: typing.TypeAlias = Callable[
    [],
    EffectJournal,
]


if __name__ == '__main__':
    pass
