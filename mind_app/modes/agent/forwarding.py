# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
import asyncio
from loguru import logger
from engine.tinker import MindError
from ...runtime.agent_client import AgentClient
from .models import (
    AgentLiveStatus, AgentSessionRuntime
)
from .ui import start_status_animation

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


def normalize_forward_profiles(payload: dict[str, typing.Any]) -> list[str] | None:
    """把服务端 `mind.forward.payload.profile` 归一化为输入入口列表。"""
    profile_raw = payload.get("profile")
    if profile_raw in (None, ""):
        return None
    if not isinstance(profile_raw, list):
        raise ValueError("mind.forward payload.profile must be a list when provided")

    normalized: list[str] = []
    for index, item in enumerate(profile_raw):
        if not isinstance(item, str):
            raise ValueError(f"mind.forward payload.profile[{index}] must be a string")

        entry = item.strip()
        if not entry:
            raise ValueError(f"mind.forward payload.profile[{index}] must be a non-empty string")
        normalized.append(entry)

    return normalized or None


def resolve_intent_summary(payload: dict[str, typing.Any]) -> str | None:
    """提取服务端下发的任务意图摘要。"""
    intent_raw = payload.get("intent")
    if intent_raw in (None, ""):
        return None
    if not isinstance(intent_raw, dict):
        raise ValueError("mind.forward payload.intent must be an object")

    summary_raw = intent_raw.get("summary")
    if summary_raw in (None, ""):
        return None
    if not isinstance(summary_raw, str):
        raise ValueError("mind.forward payload.intent.summary must be a string")

    summary = summary_raw.strip()
    return summary or None


def normalize_forward_target(
    payload: dict[str, typing.Any]
) -> tuple[
    typing.Literal[
        "chat",
        "fast",
        "plan"
    ], str | None, list[typing.Any] | None, str | None
]:
    """解析 `mind.forward` 载荷，映射到本地可执行的模式与参数。"""
    mode_raw = payload.get("mode")
    if not isinstance(mode_raw, str):
        raise ValueError("mind.forward payload.mode must be a string")
    mode = mode_raw.strip().lower()
    if mode not in {"chat", "fast", "plan"}:
        raise ValueError("mind.forward payload.mode must be chat, fast, or plan")

    message_raw = payload.get("message")
    if message_raw in (None, ""):
        message = None
    elif isinstance(message_raw, str):
        message = message_raw
    else:
        raise ValueError("mind.forward payload.message must be a string when provided")

    metadata_raw = payload.get("metadata")
    if metadata_raw is not None and not isinstance(metadata_raw, dict):
        raise ValueError("mind.forward payload.metadata must be an object")

    profile_entries = normalize_forward_profiles(payload)
    intent_summary  = resolve_intent_summary(payload)

    if str(message or "").strip():
        return mode, message, None, intent_summary

    if profile_entries:
        return mode, None, profile_entries, intent_summary

    raise ValueError("mind.forward requires non-empty message or payload.profile")


def resolve_forward_timeout_sec(payload: dict[str, typing.Any]) -> float | None:
    """解析 `mind.forward` 的超时设置。"""
    if (raw := payload.get("timeout_sec")) in (None, ""):
        return None

    try:
        timeout_sec = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("mind.forward payload.timeout_sec must be numeric") from exc

    if timeout_sec <= 0:
        raise ValueError("mind.forward payload.timeout_sec must be greater than 0")

    return timeout_sec


async def execute_forward(
    mind: "Mind",
    *,
    client: AgentClient,
    connection: typing.Any,
    runtime: AgentSessionRuntime,
    call_id: str,
    cid: str | None,
    sid: str | None,
    payload: dict[str, typing.Any],
    live_status: AgentLiveStatus | None = None
) -> None:
    """执行一条 `mind.forward` 下发的本地任务。"""
    mode, message, profile, intent_summary = normalize_forward_target(payload)

    timeout_sec      = resolve_forward_timeout_sec(payload)
    metadata_raw     = payload.get("metadata")
    forward_metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

    metadata = dict(forward_metadata)
    if cid is not None:
        metadata["cid"] = cid
    if sid is not None:
        metadata["sid"] = sid
    if intent_summary is not None:
        metadata["intent_summary"] = intent_summary

    logger.debug(
        f"[Agent] forward start call_id={call_id} mode={mode} "
        f"message={json.dumps(message, ensure_ascii=False)} "
        f"profile={json.dumps(profile, ensure_ascii=False)} "
        f"timeout_sec={timeout_sec or 0} "
        f"metadata={json.dumps(forward_metadata, ensure_ascii=False)}"
    )
    if live_status is not None:
        live_status.update(
            "Server Task Received", f"{mode} · {call_id}"
        )

    if cid and sid:
        await client.send_mind_started(
            connection,
            session_id=runtime.session_id,
            cid=cid,
            sid=sid,
            call_id=call_id
        )

    if profile is not None:
        runner = mind.mind_pack(profile, mode, metadata=metadata)
    else:
        if message is None:
            raise MindError("mind.forward resolved empty message")
        runner = mind.calling(message=message, mode=mode, metadata=metadata)

    if timeout_sec is not None:
        await asyncio.wait_for(runner, timeout=timeout_sec)
    else:
        await runner

    if cid and sid:
        await client.send_mind_completed(
            connection,
            session_id=runtime.session_id,
            cid=cid,
            sid=sid,
            call_id=call_id
        )

    logger.debug(
        f"[Agent] forward done call_id={call_id} mode={mode}"
    )


def spawn_forward_task(
    mind: "Mind",
    client: AgentClient,
    connection: typing.Any,
    runtime: AgentSessionRuntime,
    *,
    call_id: str,
    cid: str | None,
    sid: str | None,
    payload: dict[str, typing.Any],
    live_status: AgentLiveStatus | None = None
) -> None:
    """以后台任务方式执行 `mind.forward`，避免阻塞 WS 心跳处理。"""
    tasks = runtime.pending_tasks if runtime.pending_tasks is not None else set()
    runtime.pending_tasks = tasks

    async def runner() -> None:
        try:
            await execute_forward(
                mind,
                client=client,
                connection=connection,
                runtime=runtime,
                call_id=call_id,
                cid=cid,
                sid=sid,
                payload=payload,
                live_status=live_status
            )
        except asyncio.CancelledError:
            logger.debug(
                f"[Agent] forward cancelled call_id={call_id}"
            )
            if live_status is not None:
                live_status.update(
                    "Exiting Subscription", "Canceled in-flight local task"
                )
            raise
        except Exception as exc:
            logger.debug(
                f"[Agent] forward failed call_id={call_id}: {type(exc).__name__}: {exc}"
            )
            if cid and sid:
                await client.send_mind_failed(
                    connection,
                    session_id=runtime.session_id,
                    cid=cid,
                    sid=sid,
                    call_id=call_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc)
                )
            if live_status is not None:
                live_status.update(
                    "Task Execution Failed", f"{call_id} · {type(exc).__name__}"
                )
        finally:
            if live_status is not None and not mind.task_event.is_set():
                live_status.update(
                    "Waiting for Server Tasks", "Long link established and listening"
                )
                await start_status_animation(mind, live_status)

    task = asyncio.create_task(runner(), name=f"agent-forward-{call_id or 'unknown'}")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


if __name__ == '__main__':
    pass
