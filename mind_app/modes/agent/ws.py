# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from loguru import logger
from ...runtime.agent_client import AgentClient
from .models import (
    AgentSessionRuntime, AgentLiveStatus
)
from .forwarding import spawn_forward_task

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


def update_last_acked_seq(runtime: AgentSessionRuntime, message: dict[str, typing.Any]) -> None:
    """记录当前已观测到的服务端最新序号。"""
    seq = message.get("seq")
    if isinstance(seq, int) and seq > runtime.last_acked_seq:
        runtime.last_acked_seq = seq


def get_runtime_message_cache(runtime: AgentSessionRuntime) -> set[str]:
    """返回订阅运行态中的转发消息去重集合。"""
    if runtime.forwarded_message_ids is None:
        runtime.forwarded_message_ids = set()
    return runtime.forwarded_message_ids


async def cancel_runtime_tasks(runtime: AgentSessionRuntime) -> None:
    """取消并回收当前订阅会话中的后台任务。"""
    tasks = runtime.pending_tasks
    if not tasks:
        return None

    for task in list(tasks):
        task.cancel()

    for task in list(tasks):
        with contextlib.suppress(asyncio.CancelledError):
            await task

    tasks.clear()


async def recv_json_or_stop(
    client: AgentClient,
    connection: typing.Any,
    stop_event: asyncio.Event,
) -> dict[str, typing.Any]:
    """在等待 WS 消息时同时响应退出信号。"""
    recv_task = asyncio.create_task(client.recv_json(connection))
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, pending = await asyncio.wait(
            {recv_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done:
            recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await recv_task
            raise asyncio.CancelledError

        return await recv_task
    finally:
        for task in (recv_task, stop_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def sleep_or_stop(delay_sec: float, stop_event: asyncio.Event) -> None:
    """在退避等待期间同时响应退出信号。"""
    sleep_task = asyncio.create_task(asyncio.sleep(delay_sec))
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, pending = await asyncio.wait(
            {sleep_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done:
            sleep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sleep_task
            raise asyncio.CancelledError

        await sleep_task
    finally:
        for task in (sleep_task, stop_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


def summarize_ws_message(message: dict[str, typing.Any]) -> str:
    """把 WS 消息压缩成简短摘要，避免日志刷出整包 JSON。"""
    message_type = str(message.get("type") or "")
    parts = [f"type={message_type or '-'}"]

    seq = message.get("seq")
    if isinstance(seq, int):
        parts.append(f"seq={seq}")

    message_id = message.get("message_id")
    if isinstance(message_id, str) and message_id:
        parts.append(f"message_id={message_id}")

    cid = message.get("cid")
    if isinstance(cid, str) and cid:
        parts.append(f"cid={cid}")

    sid = message.get("sid")
    if isinstance(sid, str) and sid:
        parts.append(f"sid={sid}")

    payload_raw = message.get("payload")
    payload = payload_raw if isinstance(payload_raw, dict) else {}

    call_id = payload.get("call_id")
    if isinstance(call_id, str) and call_id:
        parts.append(f"call_id={call_id}")

    mode = payload.get("mode")
    if isinstance(mode, str) and mode:
        parts.append(f"mode={mode}")

    status = payload.get("status")
    if isinstance(status, str) and status:
        parts.append(f"status={status}")

    code = payload.get("code")
    if isinstance(code, str) and code:
        parts.append(f"code={code}")

    return " ".join(parts)


async def handle_server_message(
    mind: "Mind",
    client: AgentClient,
    connection: typing.Any,
    runtime: AgentSessionRuntime,
    message: dict[str, typing.Any],
    live_status: AgentLiveStatus,
) -> None:
    """按订阅协议处理一条服务端消息。"""
    update_last_acked_seq(runtime, message)

    message_type = str(message.get("type") or "")

    if message_type == "ready":
        payload_raw = message.get("payload")
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        live_status.update("Subscription Online", "Handshake complete, waiting for tasks")
        logger.debug(
            "[Agent] ready "
            f"heartbeat_interval_sec={payload.get('heartbeat_interval_sec')} "
            f"heartbeat_timeout_sec={payload.get('heartbeat_timeout_sec')} "
            f"resume_timeout_sec={payload.get('resume_timeout_sec')}"
        )
        return None

    if message_type == "ping":
        live_status.update("Link Heartbeat", "Ping received, replying with pong")
        await client.send_pong(connection, session_id=runtime.session_id)
        logger.debug("[Agent] pong sent")
        return None

    if message_type == "replay.batch":
        payload_raw  = message.get("payload")
        payload      = payload_raw if isinstance(payload_raw, dict) else {}
        messages_raw = payload.get("messages")
        replayed     = messages_raw if isinstance(messages_raw, list) else []

        live_status.update(
            "Replaying History", f"replay.batch × {len(replayed)}"
        )
        logger.debug(
            f"[Agent] replay.batch count={len(replayed)}"
        )
        for replay_message in replayed:
            if isinstance(replay_message, dict):
                await handle_server_message(
                    mind,
                    client,
                    connection,
                    runtime,
                    replay_message,
                    live_status
                )
        return None

    if message_type == "mind.forward":
        payload_raw = message.get("payload")
        payload     = payload_raw if isinstance(payload_raw, dict) else {}

        message_id_raw = message.get("message_id")
        message_id     = message_id_raw if isinstance(message_id_raw, str) else ""

        call_id_raw = payload.get("call_id")
        call_id     = call_id_raw if isinstance(call_id_raw, str) else ""

        cid_raw = message.get("cid")
        cid     = cid_raw if isinstance(cid_raw, str) else None
        sid_raw = message.get("sid")
        sid     = sid_raw if isinstance(sid_raw, str) else None

        if not message_id:
            logger.debug(
                "[Agent] mind.forward ignored: message_id missing"
            )
            return None
        if not call_id:
            logger.debug(
                f"[Agent] mind.forward ignored: call_id missing message_id={message_id}"
            )
            return None
        if not cid or not sid:
            logger.debug(
                f"[Agent] mind.forward ignored: cid/sid missing call_id={call_id} message_id={message_id}"
            )
            return None

        live_status.update(
            "Task Accepted", f"Validated {call_id}, stopping live status"
        )
        await mind.await_cleanup(mind.stop_anim())

        await client.send_mind_received(
            connection,
            session_id=runtime.session_id,
            cid=cid,
            sid=sid,
            call_id=call_id,
            acked_message_id=message_id
        )
        logger.debug(
            f"[Agent] mind.received sent call_id={call_id} message_id={message_id}"
        )

        seen = get_runtime_message_cache(runtime)
        if message_id in seen:
            logger.warning(
                f"[Agent] mind.forward replay skipped message_id={message_id}"
            )
            return None

        seen.add(message_id)
        spawn_forward_task(
            mind,
            runtime,
            call_id=call_id,
            cid=cid,
            sid=sid,
            payload=payload,
            live_status=live_status
        )
        return None

    if message_type == "ack":
        return None

    if message_type == "error":
        payload_raw = message.get("payload")
        payload     = payload_raw if isinstance(payload_raw, dict) else {}
        logger.error(
            f"[Agent] server error code={payload.get('code') or ''} message={payload.get('message') or ''}"
        )
        return None

    if message_type == "pong":
        return None

    logger.debug(
        f"[Agent] unsupported ws message type={message_type}"
    )


async def connect_once(
    mind: "Mind",
    client: AgentClient,
    runtime: AgentSessionRuntime,
    live_status: AgentLiveStatus
) -> None:
    """建立一次 WS 连接生命周期，并持续处理消息直到断开。"""
    async with await client.connect_ws(
        session_id=runtime.session_id,
        ws_token=runtime.ws_token,
        ws_base_url=runtime.ws_url
    ) as connection:
        live_status.update(
            "Opening Long Link", "WebSocket connected, sending hello"
        )
        if not runtime.hello_sent:
            await client.send_hello(
                connection,
                session_id=runtime.session_id,
                device_id=runtime.device_id,
                client_version=runtime.client_version,
            )
            runtime.hello_sent = True
            logger.debug("[Agent] hello sent")

        if runtime.last_acked_seq > 0:
            live_status.update(
                "Resuming Session", f"Sending resume · seq={runtime.last_acked_seq}"
            )
            await client.send_resume(
                connection,
                session_id=runtime.session_id,
                last_acked_seq=runtime.last_acked_seq
            )
            logger.debug(
                f"[Agent] resume sent last_acked_seq={runtime.last_acked_seq}"
            )

        while True:
            message = await recv_json_or_stop(client, connection, mind.task_event)
            logger.debug(
                f"[Agent] recv {summarize_ws_message(message)}"
            )
            await handle_server_message(mind, client, connection, runtime, message, live_status)


if __name__ == '__main__':
    pass
