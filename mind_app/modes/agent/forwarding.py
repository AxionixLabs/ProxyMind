# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
import asyncio
from loguru import logger
from .models import (
    AgentLiveStatus, AgentSessionRuntime
)
from .ui import start_status_animation

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


def normalize_forward_target(
    payload: dict[str, typing.Any]
) -> tuple[typing.Literal["chat", "fast", "plan"], str, str]:
    """解析 `mind.forward` 载荷，映射到本地可执行的模式与参数。"""
    allowed = {
        "call_id", "mode", "profile", "message", "metadata", "timeout_sec"
    }
    extra = set(payload) - allowed
    if extra:
        raise ValueError(f"Unsupported mind.forward payload field(s): {', '.join(sorted(extra))}")

    mode_raw = payload.get("mode")
    if not isinstance(mode_raw, str):
        raise ValueError("mind.forward payload.mode must be a string")
    mode = mode_raw.strip().lower()
    if mode not in {"chat", "fast", "plan"}:
        raise ValueError("mind.forward payload.mode must be chat, fast, or plan")

    profile_raw = payload.get("profile")
    if not isinstance(profile_raw, str):
        raise ValueError("mind.forward payload.profile must be a string")
    profile = profile_raw.strip().lower()
    if profile not in {"", "code"}:
        raise ValueError("mind.forward payload.profile must be empty or code")

    message_raw = payload.get("message")
    if not isinstance(message_raw, str):
        raise ValueError("mind.forward payload.message must be a string")
    if not message_raw.strip():
        raise ValueError("mind.forward payload.message is required")

    metadata_raw = payload.get("metadata")
    if metadata_raw is not None and not isinstance(metadata_raw, dict):
        raise ValueError("mind.forward payload.metadata must be an object")

    return typing.cast(typing.Literal["chat", "fast", "plan"], mode), profile, message_raw


def resolve_forward_timeout_sec(payload: dict[str, typing.Any]) -> float | None:
    """解析 `mind.forward` 的超时设置。"""
    raw = payload.get("timeout_sec")
    if raw in (None, ""):
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
    call_id: str,
    cid: str | None,
    sid: str | None,
    payload: dict[str, typing.Any],
    live_status: AgentLiveStatus | None = None,
) -> None:
    """执行一条 `mind.forward` 下发的本地任务。"""
    mode, profile, message = normalize_forward_target(payload)

    timeout_sec      = resolve_forward_timeout_sec(payload)
    metadata_raw     = payload.get("metadata")
    forward_metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

    metadata = dict(forward_metadata)
    if cid is not None:
        metadata["cid"] = cid
    if sid is not None:
        metadata["sid"] = sid

    logger.debug(
        f"[Agent] forward start call_id={call_id} mode={mode} profile={profile or '-'} "
        f"message={json.dumps(message, ensure_ascii=False)} timeout_sec={timeout_sec or 0} "
        f"metadata={json.dumps(forward_metadata, ensure_ascii=False)}"
    )
    if live_status is not None:
        live_status.update(
            "Server Task Received", f"{mode}/{profile or 'default'} · {call_id}"
        )

    if profile == "code":
        runner = mind.mind_pack([message], mode, metadata=metadata)
    else:
        runner = mind.calling(message=message, mode=mode, metadata=metadata)

    if timeout_sec is not None:
        await asyncio.wait_for(runner, timeout=timeout_sec)
    else:
        await runner

    logger.debug(
        f"[Agent] forward done call_id={call_id} mode={mode} profile={profile or '-'}"
    )


def spawn_forward_task(
    mind: "Mind",
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
