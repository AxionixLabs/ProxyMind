# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from engine.observability import observe
from ...runtime.agent.client import AgentClient
from mind_nova.requests.payload import empty_primary_request_slot
from .models import (
    AgentForwardRequest,
    AgentSessionRuntime,
    AgentLiveStatus
)
from .forwarding import (
    AgentForwardHandler,
    AutoForwardHandler
)

if typing.TYPE_CHECKING:
    from ...controller import Mind


_REOPEN_ERROR_CODES: typing.Final[set[str]] = {
    "AGENT_TOKEN_INVALID",
    "AGENT_CLIENT_UNAUTHORIZED",
    "AGENT_WS_SCOPE_INVALID",
    "AGENT_WS_SESSION_MISMATCH",
    "AGENT_WS_DEVICE_MISMATCH",
    "AGENT_SESSION_NOT_FOUND",
    "AGENT_SESSION_OFFLINE",
    "AGENT_SESSION_NOT_CONNECTED",
    "AGENT_SESSION_UNRESOLVED",
    "AGENT_SESSION_AMBIGUOUS"
}

_ABORT_ERROR_CODES: typing.Final[set[str]] = {
    "AGENT_WS_PAYLOAD_INVALID"
}


class AgentWsProtocolError(RuntimeError):
    """服务端通过 WS `error` 帧显式拒绝当前会话时抛出的异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        action: typing.Literal["reconnect", "reopen", "abort"] = "reconnect"
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message_text = message
        self.action = action


def extract_message_seq(message: dict[str, typing.Any]) -> int | None:
    """读取服务端消息序号。"""
    seq = message.get("seq")
    return seq if isinstance(seq, int) else None


def update_last_acked_seq(runtime: AgentSessionRuntime, seq: int | None) -> None:
    """仅在消息被成功处理后推进恢复水位。"""
    if isinstance(seq, int) and seq > runtime.last_acked_seq:
        runtime.last_acked_seq = seq


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
    stop_event: asyncio.Event
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


async def build_runtime_llm_conf(mind: "Mind") -> dict[str, typing.Any]:
    """基于当前偏好配置生成 `runtime.bind` 所需的 llm_conf。"""
    payload     = await mind.fresh_pref_config(ttl_sec=0.0)
    primary_raw = payload.get("primary")
    primary     = primary_raw if isinstance(primary_raw, dict) else {}

    primary_conf: dict[str, typing.Any] = {}
    if primary.get("enabled") is False:
        return {"primary": empty_primary_request_slot()}

    provider = str(primary.get("provider", "") or "").strip()
    if provider:
        primary_conf["provider"] = provider

    route = str(primary.get("route", "") or "").strip()
    if route:
        primary_conf["route"] = route

    for key in ("model", "apikey", "base_url", "reasoning_effort"):
        value = str(primary.get(key, "") or "").strip()
        if value:
            primary_conf[key] = value

    return {"primary": primary_conf}


def parse_forward_request(
    runtime: AgentSessionRuntime,
    message: dict[str, typing.Any]
) -> AgentForwardRequest | None:
    """解析并校验服务端下发的 forward 请求。"""
    payload_raw    = message.get("payload")
    payload        = payload_raw if isinstance(payload_raw, dict) else {}
    session_id_raw = message.get("session_id")
    session_id     = session_id_raw if isinstance(session_id_raw, str) else ""

    message_id_raw = message.get("message_id")
    message_id     = message_id_raw if isinstance(message_id_raw, str) else ""

    call_id_raw = payload.get("call_id")
    call_id     = call_id_raw if isinstance(call_id_raw, str) else ""

    cid_raw = message.get("cid")
    cid     = cid_raw if isinstance(cid_raw, str) else None
    sid_raw = message.get("sid")
    sid     = sid_raw if isinstance(sid_raw, str) else None

    if not message_id:
        observe("agent.forward.ignored", level="WARNING", reason="message_id_missing")
        return None
    if not session_id or session_id != runtime.session_id:
        raise AgentWsProtocolError(
            "AGENT_WS_SESSION_MISMATCH",
            f"mind.forward session_id mismatch expected={runtime.session_id} actual={session_id or '-'}",
            action="reopen"
        )
    if not call_id:
        observe(
            "agent.forward.ignored",
            level="WARNING",
            reason="call_id_missing",
            message_id=message_id,
        )
        return None
    if not cid or not sid:
        observe(
            "agent.forward.ignored",
            level="WARNING",
            reason="conversation_ids_missing",
            call_id=call_id,
            message_id=message_id,
        )
        return None

    observe(
        "agent.forward.accepted",
        call_id=call_id,
        message_id=message_id,
        cid=cid,
        sid=sid,
    )
    return AgentForwardRequest(
        message_id=message_id,
        call_id=call_id,
        cid=cid,
        sid=sid,
        payload=payload
    )


async def handle_server_message(
    mind: "Mind",
    client: AgentClient,
    connection: typing.Any,
    runtime: AgentSessionRuntime,
    message: dict[str, typing.Any],
    live_status: AgentLiveStatus,
    forward_handler: AgentForwardHandler | None = None
) -> int | None:
    """按订阅协议处理一条服务端消息。"""
    current_seq  = extract_message_seq(message)
    message_type = str(message.get("type") or "")

    if message_type == "ready":
        payload_raw = message.get("payload")
        payload     = payload_raw if isinstance(payload_raw, dict) else {}

        runtime.ready_received = True
        runtime.pre_ready_connect_failures = 0

        live_status.update("Subscription Online", "Handshake complete, waiting for tasks")

        observe(
            "agent.ws.ready",
            heartbeat_interval_sec=payload.get("heartbeat_interval_sec"),
            heartbeat_timeout_sec=payload.get("heartbeat_timeout_sec"),
            resume_timeout_sec=payload.get("resume_timeout_sec"),
        )
        return current_seq

    if message_type == "ping":
        live_status.update("Link Heartbeat", "Ping received, replying with pong")
        await client.send_pong(connection, session_id=runtime.session_id)
        observe("agent.ws.pong_sent", session_id=runtime.session_id)
        return current_seq

    if message_type == "replay.batch":
        payload_raw  = message.get("payload")
        payload      = payload_raw if isinstance(payload_raw, dict) else {}
        messages_raw = payload.get("messages")
        replayed     = messages_raw if isinstance(messages_raw, list) else []

        live_status.update(
            "Replaying History", f"replay.batch × {len(replayed)}"
        )
        observe("agent.ws.replay", count=len(replayed))
        handled_seq = current_seq
        for replay_message in replayed:
            if isinstance(replay_message, dict):
                replay_seq = await handle_server_message(
                    mind,
                    client,
                    connection,
                    runtime,
                    replay_message,
                    live_status,
                    forward_handler
                )
                if isinstance(replay_seq, int):
                    handled_seq = replay_seq if handled_seq is None else max(handled_seq, replay_seq)
        return handled_seq

    if message_type == "resume.result":
        live_status.update(
            "Resume Confirmed", "Server accepted resume, waiting for replay"
        )
        observe("agent.ws.resume.accepted", session_id=runtime.session_id)
        return current_seq

    if message_type == "resume.rejected":
        payload_raw  = message.get("payload")
        payload      = payload_raw if isinstance(payload_raw, dict) else {}
        code         = str(payload.get("code") or "AGENT_RESUME_REJECTED").strip()
        message_text = str(payload.get("message") or "resume rejected").strip()

        observe(
            "agent.ws.resume.rejected",
            level="WARNING",
            code=code,
            message=message_text,
        )
        raise AgentWsProtocolError(code, message_text, action="reopen")

    if message_type == "mind.forward":
        request = parse_forward_request(runtime, message)
        if request is None:
            return current_seq

        handler = forward_handler or AutoForwardHandler()
        await handler.handle(
            mind,
            client,
            connection,
            runtime,
            request,
            live_status
        )
        return current_seq

    if message_type == "ack":
        return current_seq

    if message_type == "error":
        payload_raw  = message.get("payload")
        payload      = payload_raw if isinstance(payload_raw, dict) else {}
        message_text = str(payload.get("message") or "").strip()

        code = str(payload.get("code") or "").strip()

        observe(
            "agent.ws.server_error",
            level="WARNING",
            code=code,
            message=message_text,
        )
        if code in _ABORT_ERROR_CODES:
            raise AgentWsProtocolError(code, message_text, action="abort")
        if code in _REOPEN_ERROR_CODES:
            raise AgentWsProtocolError(code, message_text, action="reopen")
        return current_seq

    if message_type == "pong":
        return current_seq

    observe("agent.ws.unsupported", level="WARNING", message_type=message_type)
    return current_seq


async def connect_once(
    mind: "Mind",
    client: AgentClient,
    runtime: AgentSessionRuntime,
    live_status: AgentLiveStatus,
    forward_handler: AgentForwardHandler | None = None
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
        await client.send_hello(
            connection,
            session_id=runtime.session_id,
            device_id=runtime.device_id,
            client_version=runtime.client_version,
        )
        observe("agent.ws.hello_sent", session_id=runtime.session_id)

        await client.send_runtime_bind(
            connection,
            session_id=runtime.session_id,
            llm_conf=await build_runtime_llm_conf(mind)
        )
        observe(
            "agent.ws.bound",
            session_id=runtime.session_id,
            ready_received=runtime.ready_received,
            last_acked_seq=runtime.last_acked_seq,
            resume_available=bool(runtime.resume_token),
        )

        if runtime.last_acked_seq > 0:
            live_status.update(
                "Resuming Session", f"Sending resume · seq={runtime.last_acked_seq}"
            )
            await client.send_resume(
                connection,
                session_id=runtime.session_id,
                last_acked_seq=runtime.last_acked_seq
            )
            observe(
                "agent.ws.resume.sent",
                session_id=runtime.session_id,
                last_acked_seq=runtime.last_acked_seq,
            )
        else:
            observe(
                "agent.ws.resume.skipped",
                session_id=runtime.session_id,
                reason="no_acked_sequence",
            )

        while True:
            message = await recv_json_or_stop(client, connection, mind.task_event)
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            observe(
                "agent.ws.received",
                message_type=message.get("type"),
                seq=message.get("seq"),
                message_id=message.get("message_id"),
                call_id=payload.get("call_id"),
            )
            handled_seq = await handle_server_message(
                mind, client, connection, runtime, message, live_status, forward_handler
            )
            update_last_acked_seq(runtime, handled_seq)


if __name__ == '__main__':
    pass
