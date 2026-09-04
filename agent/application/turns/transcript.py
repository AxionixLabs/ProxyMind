# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from collections.abc import Mapping

from agent.application.turns.execution import TurnExecution
from agent.ports.transcript import TranscriptSink

__all__ = (
    "build_turn_input_payload",
    "record_turn_finished",
    "record_turn_started",
)


def build_turn_input_payload(
    message: str,
    *,
    attachments: typing.Iterable[typing.Mapping[str, typing.Any]] = (),
    extras: typing.Mapping[str, typing.Any] | None = None,
) -> dict[str, typing.Any]:
    """构建轮次记录使用的用户输入载荷。"""
    payload: dict[str, typing.Any] = {"content": str(message)}

    attachment_items = [
        dict(item)
        for item in attachments
        if isinstance(item, Mapping)
    ]
    if attachment_items:
        payload["attachments"] = attachment_items
    if isinstance(extras, Mapping) and extras:
        payload["extras"] = dict(extras)

    return payload


def record_turn_started(
    transcript: TranscriptSink,
    execution: TurnExecution,
) -> None:
    """写入会话边界、轮次边界和用户输入。"""
    context = execution.context

    if context.session_started:
        session_payload: dict[str, typing.Any] = {
            "cwd": context.cwd,
            "source": context.source,
            "reason": context.session_start_reason,
            "model": context.model,
        }
        if context.agent.depth > 0:
            session_payload.update({
                "parent_session_id": context.agent.root_session_id,
                "agent_id": context.agent.agent_id,
                "agent_type": context.agent.agent_type,
                "task_name": context.agent.task_name,
                "task_path": context.agent.task_path,
            })
        transcript.append(
            "session.started",
            actor="system",
            payload=session_payload,
        )

    transcript.append("turn.started", actor="system")
    transcript.append(
        "message.created",
        actor="user",
        payload=dict(execution.input_payload),
    )


def record_turn_finished(
    transcript: TranscriptSink,
    *,
    status: str,
    usage: typing.Mapping[str, typing.Any] | None = None,
    error: str | None = None,
    terminal_meta: typing.Mapping[str, typing.Any] | None = None,
) -> None:
    """写入轮次的稳定终态。"""
    normalized_status = str(status or "failed").strip() or "failed"

    event = (
        "turn.interrupted"
        if normalized_status in {"interrupted", "cancelled"}
        else "turn.completed"
        if normalized_status == "completed"
        else "turn.incomplete"
        if normalized_status == "incomplete"
        else "turn.reconciliation_required"
        if normalized_status == "reconciliation_required"
        else "turn.failed"
    )

    payload: dict[str, typing.Any] = {
        "status": normalized_status,
        "usage": copy.deepcopy(dict(usage or {})),
    }
    payload.update(copy.deepcopy(dict(terminal_meta or {})))
    if error:
        payload["error"] = str(error)

    transcript.append(event, actor="system", payload=payload)


if __name__ == '__main__':
    pass
