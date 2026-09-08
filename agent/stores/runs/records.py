# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import typing

from agent.domain import (
    RECOVERABLE_RUN_STATUSES,
    RunStatus,
    recovery_action,
)
from agent.ports import RunSnapshot
from agent.protocol import (
    RunCommand,
    RunEvent,
    parse_run_command,
)
from agent.protocol.json_value import thaw_json
from .schema import RUN_SNAPSHOT_VERSION

_EVENT_STATUSES = {
    "run_queued": RunStatus.QUEUED,
    "run_started": RunStatus.RUNNING,
    "run_waiting_approval": RunStatus.WAITING_APPROVAL,
    "run_waiting_effect": RunStatus.WAITING_EFFECT,
    "run_paused": RunStatus.PAUSED,
    "run_completed": RunStatus.COMPLETED,
    "run_failed": RunStatus.FAILED,
    "run_incomplete": RunStatus.INCOMPLETE,
    "run_interrupted": RunStatus.INTERRUPTED,
    "run_cancelled": RunStatus.CANCELLED,
    "run_reconciliation_required": RunStatus.RECONCILIATION_REQUIRED,
}


def encode_json(value: typing.Any) -> str:
    """使用确定性 JSON 编码持久化协议值。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def run_status_for_event(kind: str) -> RunStatus | None:
    """把持久化事件类型解析为唯一 Run 状态。"""
    return _EVENT_STATUSES.get(kind)


def run_effect_id(command: RunCommand, fingerprint: str) -> str:
    """从 Run 坐标和命令指纹派生稳定 outbox 效果身份。"""
    material = (
        f"{command.session_id}\0{command.run_id}\0{fingerprint}"
    ).encode("utf-8")
    return f"effect_run_{hashlib.sha256(material).hexdigest()[:40]}"


def outbox_update(
    event: RunEvent,
    status: RunStatus,
) -> tuple[str, str | None, str]:
    """把 Run 状态映射为 outbox 状态、结果和错误摘要。"""
    if status is RunStatus.RUNNING:
        return "dispatching", None, ""
    if status is RunStatus.COMPLETED:
        return "committed", _result_json(event), ""
    if status is RunStatus.RECONCILIATION_REQUIRED:
        return "reconciliation_required", _result_json(event), _event_error(event)
    if status in {RunStatus.CANCELLED, RunStatus.INTERRUPTED}:
        return "cancelled", _result_json(event), _event_error(event)
    if status in {RunStatus.FAILED, RunStatus.INCOMPLETE}:
        return "failed", _result_json(event), _event_error(event)
    return str(event.payload.get("status") or status.value), None, ""


def terminal_facts(
    result: dict[str, typing.Any],
) -> tuple[tuple[str, int, dict[str, typing.Any]], ...]:
    """把终态结果展开为稳定、具名的可查询事实。"""
    facts: list[tuple[str, int, dict[str, typing.Any]]] = [
        ("final_result", 0, result),
    ]
    assistant_text = result.get("assistant_text")
    if isinstance(assistant_text, str):
        facts.append(("assistant_message", 0, {"text": assistant_text}))
    for field_name, kind in (
            ("tool_results", "tool_result"),
            ("approval_decisions", "approval_decision"),
            ("evidence_references", "evidence_reference"),
            ("evidence_refs", "evidence_reference"),
    ):
        values = result.get(field_name)
        if not isinstance(values, (list, tuple)):
            continue
        for position, value in enumerate(values):
            payload = (
                dict(value)
                if isinstance(value, typing.Mapping)
                else {"value": value}
            )
            facts.append((kind, position, payload))
    return tuple(facts)


def snapshot_from_row(
    row: typing.Mapping[str, typing.Any],
) -> RunSnapshot:
    """把数据库行还原为独立于 SQLite 的恢复快照。"""
    snapshot_version = int(row["snapshot_version"])
    if snapshot_version != RUN_SNAPSHOT_VERSION:
        raise RuntimeError("agent runtime snapshot version is unsupported")
    status = RunStatus(str(row["status"]))
    action = (
        recovery_action(status)
        if status in RECOVERABLE_RUN_STATUSES
        else None
    )
    return RunSnapshot(
        command=parse_run_command(
            json.loads(str(row["command_json"]))
        ),
        status=status,
        sequence=int(row["sequence"]),
        snapshot_version=snapshot_version,
        effect_id=str(row["effect_id"]),
        effect_status=str(row["effect_status"]),
        recovery_action=action,
        updated_at=str(row["updated_at"]),
    )


def _result_json(event: RunEvent) -> str | None:
    """编码终态结果，缺失时保持空值。"""
    result = event.payload.get("result")
    return (
        encode_json(thaw_json(result))
        if isinstance(result, typing.Mapping)
        else None
    )


def _event_error(event: RunEvent) -> str:
    """提取可观测但不参与状态判断的错误摘要。"""
    error = event.payload.get("error")
    if isinstance(error, typing.Mapping):
        return str(error.get("message") or "")[:2000]
    return str(error or "")[:2000]


if __name__ == '__main__':
    pass
