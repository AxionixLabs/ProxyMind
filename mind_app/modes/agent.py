# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import uuid
import httpx
import socket
import typing
import asyncio
import hashlib
import platform
import contextlib
from dataclasses import dataclass
from loguru import logger
from websockets.exceptions import ConnectionClosed
from mind_core.design import Design
from ..runtime.agent_client import AgentClient
from mind_nova import const

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


@dataclass(slots=True)
class AgentResidentConfig:
    """驻留模式配置：统一收敛命令行和运行时默认值。"""
    base_url: str
    device_id: str
    agent_id: str
    client_version: str
    platform: str
    arch: str


@dataclass(slots=True)
class AgentSessionRuntime:
    """驻留会话运行态：保存断线恢复所需的动态状态。"""
    session_id: str
    ws_token: str
    resume_token: str | None
    access_token: str | None
    ws_url: str | None
    device_id: str
    client_version: str
    hello_sent: bool = False
    last_acked_seq: int = 0
    forwarded_message_ids: set[str] | None = None
    pending_tasks: set[asyncio.Task[None]] | None = None


def _build_device_id() -> str:
    """为当前设备生成稳定的驻留设备标识。"""
    raw = "|".join(
        [
            socket.gethostname().strip().lower(),
            hex(uuid.getnode()),
            platform.system().strip().lower(),
            platform.machine().strip().lower(),
        ]
    )
    digest = hashlib.sha1(raw.encode(const.CHARSET, errors="ignore")).hexdigest()[:16]
    return f"dev_{digest}"


def _build_external_api_examples(base_url: str, access_token: str) -> list[str]:
    """构造外部调用示例，便于直接调试服务端下发的访问令牌。"""
    chat_request_id = str(uuid.uuid4())
    code_request_id = str(uuid.uuid4())
    mind_chat_payload = {
        "mode"        : "chat",
        "profile"     : "",
        "subject"     : "",
        "message"     : "帮我整理今天的会议纪要",
        "timeout_sec" : 300,
        "metadata"    : {"biz_id": "biz_002"},
    }
    mind_code_payload = {
        "mode"        : "plan",
        "profile"     : "code",
        "subject"     : "order_reconcile",
        "message"     : "",
        "timeout_sec" : 300,
        "metadata"    : {"biz_id": "biz_003"},
    }

    mind_chat_body = json.dumps(mind_chat_payload, ensure_ascii=False, indent=2)
    mind_code_body = json.dumps(mind_code_payload, ensure_ascii=False, indent=2)

    return [
        (
            f"curl -X POST {base_url.rstrip('/')}/mind \\\n"
            f"  -H 'Authorization: Bearer {access_token}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -H 'Idempotency-Key: {chat_request_id}' \\\n"
            f"  -d '{mind_chat_body}'"
        ),
        (
            f"curl -X POST {base_url.rstrip('/')}/mind \\\n"
            f"  -H 'Authorization: Bearer {access_token}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -H 'Idempotency-Key: {code_request_id}' \\\n"
            f"  -d '{mind_code_body}'"
        ),
    ]


def _log_external_access(runtime: AgentSessionRuntime, base_url: str) -> None:
    """打印服务端下发的外部访问令牌和接口调用示例。"""
    if not runtime.access_token:
        logger.warning("[Agent] access token missing")
        return None

    examples = "\n\n".join(_build_external_api_examples(base_url, runtime.access_token))
    Design.console.print(f"\n{examples}\n")


def _normalize_open_payload(
    client: AgentClient,
    opened: dict[str, typing.Any],
) -> tuple[str, str, str | None, str | None, str | None]:
    """从 `/agents/open` 响应中提取会话与握手信息。"""
    data        = client.unwrap_data(opened)
    session_raw = data.get("session")
    session     = session_raw if isinstance(session_raw, dict) else {}

    session_id = data.get("session_id") or session.get("session_id")

    ws_token   = data.get("ws_token")
    ws_url_raw = data.get("ws_url")
    ws_url     = ws_url_raw if isinstance(ws_url_raw, str) else None

    resume_token_raw = data.get("resume_token")
    resume_token     = resume_token_raw if isinstance(resume_token_raw, str) else None

    access_token_raw   = data.get("access_token")
    access_token_data  = access_token_raw if isinstance(access_token_raw, dict) else {}
    access_token_token = access_token_data.get("token")
    access_token       = access_token_token if isinstance(access_token_token, str) else None

    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("agent open response missing session_id or ws_token")
    if not isinstance(ws_token, str) or not ws_token:
        raise RuntimeError("agent open response missing session_id or ws_token")

    return session_id, ws_token, ws_url, resume_token, access_token


async def _open_with_fallback(
    client: AgentClient,
    config: AgentResidentConfig,
) -> tuple[dict[str, typing.Any], str]:
    """使用固定 device_id 发起 open；409 冲突时把服务端错误细节打出来。"""
    try:
        opened = await client.open_session(
            device_id=config.device_id,
            agent_id=config.agent_id,
            client_version=config.client_version,
            platform=config.platform,
            arch=config.arch
        )
        return opened, config.device_id
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

        _log_http_error_detail("[Agent] open conflict", exc)

        raise


async def _open_runtime(
    client: AgentClient,
    config: AgentResidentConfig
) -> tuple[dict[str, typing.Any], str] | None:
    """持续重试 open，直到成功创建会话。"""
    while True:
        try:
            return await _open_with_fallback(client, config)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                logger.warning(
                    "[Agent] open conflict persists; "
                    "the server may still hold another session for this agent. retrying in 5s"
                )
                await asyncio.sleep(5.0)
                continue
            raise
        except (OSError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.warning(f"[Agent] open failed: {type(exc).__name__}: {exc}. retrying in 5s")
            await asyncio.sleep(5.0)
            continue


def _extract_http_error_detail(exc: httpx.HTTPStatusError) -> dict[str, typing.Any]:
    """从 HTTP 错误响应中提取结构化业务错误信息。"""
    try:
        payload = exc.response.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    for key in ("detail", "details", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    return payload


def _log_http_error_detail(prefix: str, exc: httpx.HTTPStatusError) -> None:
    """记录 detail.code / detail.message / detail.extra 以便排查问题。"""
    detail     = _extract_http_error_detail(exc)
    code       = detail.get("code") if isinstance(detail, dict) else None
    message    = detail.get("message") if isinstance(detail, dict) else None
    extra      = detail.get("extra") if isinstance(detail, dict) else None
    extra_text = json.dumps(extra, ensure_ascii=False) if extra is not None else ""

    logger.warning(
        f"{prefix} status={exc.response.status_code} "
        f"code={code or ''} message={message or ''} extra={extra_text}"
    )


def _update_last_acked_seq(runtime: AgentSessionRuntime, message: dict[str, typing.Any]) -> None:
    """记录当前已观测到的服务端最新序号。"""
    seq = message.get("seq")
    if isinstance(seq, int) and seq > runtime.last_acked_seq:
        runtime.last_acked_seq = seq


def _get_runtime_message_cache(runtime: AgentSessionRuntime) -> set[str]:
    """返回驻留运行态中的转发消息去重集合。"""
    if runtime.forwarded_message_ids is None:
        runtime.forwarded_message_ids = set()
    return runtime.forwarded_message_ids


def _get_runtime_tasks(runtime: AgentSessionRuntime) -> set[asyncio.Task[None]]:
    """返回驻留运行态中的后台执行任务集合。"""
    if runtime.pending_tasks is None:
        runtime.pending_tasks = set()
    return runtime.pending_tasks


def _normalize_forward_target(
    payload: dict[str, typing.Any]
) -> tuple[typing.Literal["chat", "fast", "plan"], str, str, str]:
    """解析 `mind.forward` 载荷，映射到本地可执行的模式与参数。"""
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in {"chat", "fast", "plan"}:
        raise ValueError("mind.forward payload.mode must be chat, fast, or plan")

    profile = str(payload.get("profile") or "").strip().lower()
    if profile not in {"", "code"}:
        raise ValueError("mind.forward payload.profile must be empty or code")

    subject = str(payload.get("subject") or "").strip()
    message = str(payload.get("message") or "").strip()

    if profile == "code":
        if not subject:
            raise ValueError("mind.forward payload.subject is required when profile=code")
        return typing.cast(typing.Literal["chat", "fast", "plan"], mode), profile, subject, message

    if not message:
        raise ValueError("mind.forward payload.message is required")

    return typing.cast(typing.Literal["chat", "fast", "plan"], mode), profile, subject, message


def _resolve_forward_timeout_sec(payload: dict[str, typing.Any]) -> float | None:
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


async def _execute_forward(
    mind: "Mind",
    *,
    call_id: str,
    cid: str | None,
    sid: str | None,
    payload: dict[str, typing.Any]
) -> None:
    """执行一条 `mind.forward` 下发的本地任务。"""
    mode, profile, subject, message = _normalize_forward_target(payload)

    timeout_sec      = _resolve_forward_timeout_sec(payload)
    metadata_raw     = payload.get("metadata")
    forward_metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

    metadata = {"cid": cid, "sid": sid}

    logger.info(
        f"[Agent] forward start call_id={call_id} mode={mode} profile={profile or '-'} "
        f"subject={subject or '-'} timeout_sec={timeout_sec or 0} "
        f"metadata={json.dumps(forward_metadata, ensure_ascii=False)}"
    )

    if profile == "code":
        runner = mind.mind_pack([subject], mode, metadata=metadata)
    else:
        runner = mind.calling(message=message, mode=mode, metadata=metadata)

    if timeout_sec is not None:
        await asyncio.wait_for(runner, timeout=timeout_sec)
    else:
        await runner

    logger.info(f"[Agent] forward done call_id={call_id} mode={mode} profile={profile or '-'}")


def _spawn_forward_task(
    mind: "Mind",
    runtime: AgentSessionRuntime,
    *,
    call_id: str,
    cid: str | None,
    sid: str | None,
    payload: dict[str, typing.Any]
) -> None:
    """以后台任务方式执行 `mind.forward`，避免阻塞 WS 心跳处理。"""
    tasks = _get_runtime_tasks(runtime)

    async def runner() -> None:
        try:
            await _execute_forward(
                mind,
                call_id=call_id,
                cid=cid,
                sid=sid,
                payload=payload,
            )
        except asyncio.CancelledError:
            logger.warning(f"[Agent] forward cancelled call_id={call_id}")
            raise
        except Exception as exc:
            logger.error(f"[Agent] forward failed call_id={call_id}: {type(exc).__name__}: {exc}")

    task = asyncio.create_task(runner(), name=f"agent-forward-{call_id or 'unknown'}")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _cancel_runtime_tasks(runtime: AgentSessionRuntime) -> None:
    """取消并回收当前驻留会话中的后台任务。"""
    tasks = runtime.pending_tasks
    if not tasks:
        return None

    for task in list(tasks):
        task.cancel()

    for task in list(tasks):
        with contextlib.suppress(asyncio.CancelledError):
            await task

    tasks.clear()


async def _recv_json_or_stop(
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


async def _sleep_or_stop(delay_sec: float, stop_event: asyncio.Event) -> None:
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


def _summarize_ws_message(message: dict[str, typing.Any]) -> str:
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


async def _handle_server_message(
    mind: "Mind",
    client: AgentClient,
    connection: typing.Any,
    runtime: AgentSessionRuntime,
    message: dict[str, typing.Any]
) -> None:
    """按驻留协议处理一条服务端消息。"""
    _update_last_acked_seq(runtime, message)

    message_type = str(message.get("type") or "")

    if message_type == "ready":
        payload_raw = message.get("payload")
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        logger.info(
            "[Agent] ready "
            f"heartbeat_interval_sec={payload.get('heartbeat_interval_sec')} "
            f"heartbeat_timeout_sec={payload.get('heartbeat_timeout_sec')} "
            f"resume_timeout_sec={payload.get('resume_timeout_sec')}"
        )
        return None

    if message_type == "ping":
        await client.send_pong(connection, session_id=runtime.session_id)
        logger.info("[Agent] pong sent")
        return None

    if message_type == "replay.batch":
        payload_raw = message.get("payload")
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        messages_raw = payload.get("messages")
        replayed = messages_raw if isinstance(messages_raw, list) else []
        logger.info(f"[Agent] replay.batch count={len(replayed)}")
        for replay_message in replayed:
            if isinstance(replay_message, dict):
                await _handle_server_message(
                    mind,
                    client,
                    connection,
                    runtime,
                    replay_message,
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
            logger.warning(
                "[Agent] mind.forward ignored: message_id missing"
            )
            return None
        if not call_id:
            logger.warning(
                f"[Agent] mind.forward ignored: call_id missing message_id={message_id}"
            )
            return None
        if not cid or not sid:
            logger.warning(
                f"[Agent] mind.forward ignored: cid/sid missing call_id={call_id} message_id={message_id}"
            )
            return None

        await client.send_mind_received(
            connection,
            session_id=runtime.session_id,
            cid=cid,
            sid=sid,
            call_id=call_id,
            acked_message_id=message_id
        )
        logger.info(
            f"[Agent] mind.received sent call_id={call_id} message_id={message_id}"
        )

        seen = _get_runtime_message_cache(runtime)
        if message_id in seen:
            logger.info(f"[Agent] mind.forward replay skipped message_id={message_id}")
            return None

        seen.add(message_id)
        _spawn_forward_task(
            mind,
            runtime,
            call_id=call_id,
            cid=cid,
            sid=sid,
            payload=payload
        )
        return None

    if message_type == "ack":
        return None

    if message_type == "error":
        payload_raw = message.get("payload")
        payload     = payload_raw if isinstance(payload_raw, dict) else {}
        logger.warning(
            f"[Agent] server error code={payload.get('code') or ''} message={payload.get('message') or ''}"
        )
        return None

    if message_type == "pong":
        return None

    logger.warning(
        f"[Agent] unsupported ws message type={message_type}"
    )


async def _connect_once(
    mind: "Mind",
    client: AgentClient,
    runtime: AgentSessionRuntime
) -> None:
    """建立一次 WS 连接生命周期，并持续处理消息直到断开。"""
    async with await client.connect_ws(
        session_id=runtime.session_id,
        ws_token=runtime.ws_token,
        ws_base_url=runtime.ws_url
    ) as connection:
        if not runtime.hello_sent:
            await client.send_hello(
                connection,
                session_id=runtime.session_id,
                device_id=runtime.device_id,
                client_version=runtime.client_version,
            )
            runtime.hello_sent = True
            logger.info("[Agent] hello sent")

        if runtime.last_acked_seq > 0:
            await client.send_resume(
                connection,
                session_id=runtime.session_id,
                last_acked_seq=runtime.last_acked_seq
            )
            logger.info(f"[Agent] resume sent last_acked_seq={runtime.last_acked_seq}")

        while True:
            message = await _recv_json_or_stop(client, connection, mind.task_event)
            logger.info(f"[Agent] recv {_summarize_ws_message(message)}")
            await _handle_server_message(mind, client, connection, runtime, message)


async def _resume_or_reopen(
    client: AgentClient,
    runtime: AgentSessionRuntime,
    config: AgentResidentConfig
) -> AgentSessionRuntime:
    """优先尝试 resume；如果服务端判定不可恢复，则重新打开新会话。"""
    if not runtime.resume_token:
        raise RuntimeError("resume_token missing")

    resume_resp = await client.resume_session(
        session_id=runtime.session_id,
        resume_token=runtime.resume_token,
        last_acked_seq=runtime.last_acked_seq,
        device_id=runtime.device_id,
        agent_id=config.agent_id,
    )
    resume_data = client.unwrap_data(resume_resp)
    resumable = bool(resume_data.get("resumable"))
    logger.info(f"[Agent] resume status resumable={resumable} last_acked_seq={runtime.last_acked_seq}")

    if resumable:
        return runtime

    opened, device_id = await _open_runtime(client, config)
    session_id, ws_token, ws_url, resume_token, access_token = _normalize_open_payload(client, opened)
    reopened = AgentSessionRuntime(
        session_id=session_id,
        ws_token=ws_token,
        resume_token=resume_token,
        access_token=access_token,
        ws_url=ws_url,
        device_id=device_id,
        client_version=config.client_version,
        forwarded_message_ids=runtime.forwarded_message_ids,
        pending_tasks=runtime.pending_tasks,
    )
    logger.info(f"[Agent] reopened session_id={session_id} device_id={device_id}")
    _log_external_access(reopened, config.base_url)
    return reopened


async def agent_loop(mind: "Mind") -> None:
    """驻留模式主循环：创建会话、建立 WS，并持续处理协议消息。"""
    config = AgentResidentConfig(
        base_url=const.DOMAIN,
        device_id=_build_device_id(),
        agent_id=const.APP_NAME,
        client_version=const.APP_VERSION,
        platform=(platform.system().lower() or "windows").strip(),
        arch=(platform.machine().lower() or "amd64").strip(),
    )

    client = AgentClient(base_url=config.base_url)

    opened, device_id = await _open_runtime(client, config)
    session_id, ws_token, ws_url, resume_token, access_token = _normalize_open_payload(client, opened)
    runtime = AgentSessionRuntime(
        session_id=session_id,
        ws_token=ws_token,
        resume_token=resume_token,
        access_token=access_token,
        ws_url=ws_url,
        device_id=device_id,
        client_version=config.client_version
    )

    logger.info(f"[Agent] {config.base_url}")
    logger.info(f"[Agent] session_id={session_id} agent_id={config.agent_id} device_id={device_id}")
    _log_external_access(runtime, config.base_url)

    try:
        while not mind.task_event.is_set():
            try:
                await _connect_once(mind, client, runtime)
                return None
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.warning(f"[Agent] disconnected: {type(exc).__name__}: {exc}")

                if not runtime.resume_token:
                    logger.warning("[Agent] resume skipped: resume_token missing")
                    await _sleep_or_stop(2.0, mind.task_event)
                    continue

                try:
                    runtime = await _resume_or_reopen(client, runtime, config)
                except asyncio.CancelledError:
                    raise
                except (OSError, httpx.HTTPError, asyncio.TimeoutError) as resume_exc:
                    logger.warning(f"[Agent] resume failed: {type(resume_exc).__name__}: {resume_exc}")
                    await _sleep_or_stop(2.0, mind.task_event)
                    continue
                except Exception as resume_exc:
                    logger.error(f"[Agent] resume crashed: {type(resume_exc).__name__}: {resume_exc}")
                    await _sleep_or_stop(2.0, mind.task_event)
                    continue

                await _sleep_or_stop(1.0, mind.task_event)
    finally:
        await _cancel_runtime_tasks(runtime)


async def run_agent_loop(mind: "Mind") -> None:
    return await agent_loop(mind)


if __name__ == "__main__":
    pass
