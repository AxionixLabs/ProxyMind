# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import asyncio
import httpx
import socket
import typing
import platform
from dataclasses import dataclass
from loguru import logger
from mcp import ClientSession
from websockets.exceptions import ConnectionClosed

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
    auto_event: bool = False
    event_mode: str = "chat"
    event: dict[str, typing.Any] | None = None


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
    auto_event_sent: bool = False
    last_acked_seq: int = 0


def _serialize_tool_result(result: typing.Any) -> dict[str, typing.Any]:
    """把 MCP ToolResult 压缩成可回传的 JSON 结构。"""
    content: list[dict[str, typing.Any]] = []

    for item in list(getattr(result, "content", []) or []):
        record: dict[str, typing.Any] = {
            "type": getattr(item, "type", item.__class__.__name__.lower())
        }

        for field in ("text", "mimeType", "data"):
            value = getattr(item, field, None)
            if value is not None:
                record["mime_type" if field == "mimeType" else field] = value

        content.append(record)

    return {
        "is_error": bool(getattr(result, "isError", False)),
        "content": content,
    }


def _normalize_open_payload(
    client: AgentClient,
    opened: dict[str, typing.Any],
) -> tuple[str, str, str | None, str | None, str | None]:
    """从 `/agents/open` 响应中提取会话与握手信息。"""
    data = client.unwrap_data(opened)
    session_raw = data.get("session")
    session = session_raw if isinstance(session_raw, dict) else {}

    session_id = data.get("session_id") or session.get("session_id")
    ws_token = data.get("ws_token")
    ws_url_raw = data.get("ws_url")
    ws_url = ws_url_raw if isinstance(ws_url_raw, str) else None
    resume_token_raw = data.get("resume_token")
    resume_token = resume_token_raw if isinstance(resume_token_raw, str) else None
    access_token_raw = data.get("access_token")
    access_token_data = access_token_raw if isinstance(access_token_raw, dict) else {}
    access_token_token = access_token_data.get("token")
    access_token = access_token_token if isinstance(access_token_token, str) else None

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
            arch=config.arch,
        )
        return opened, config.device_id
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

        _log_http_error_detail("[Agent] open conflict", exc)

        raise


async def _open_runtime(
    client: AgentClient,
    config: AgentResidentConfig,
) -> tuple[dict[str, typing.Any], str]:
    """Keep retrying open until a session can be created."""
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


async def _run_mind_mode(
    mind: "Mind",
    *,
    tool_name: str,
    tool_args: dict[str, typing.Any],
    cid: str | None,
    sid: str | None,
) -> dict[str, typing.Any]:
    """桥接驻留端特殊指令到本地 mind 模式。"""
    metadata = mind.begin_session(cid=cid, sid=sid)

    if tool_name == "mind.chat":
        message = str(tool_args.get("message") or "").strip()
        if not message:
            raise ValueError("mind.chat requires arguments.message")
        await mind.calling(message=message, mode="chat", metadata=metadata)
        mode = "chat"
    elif tool_name == "mind.fast":
        message = str(tool_args.get("message") or "").strip()
        if not message:
            raise ValueError("mind.fast requires arguments.message")
        await mind.calling(message=message, mode="fast", metadata=metadata)
        mode = "fast"
    elif tool_name == "mind.plan":
        message = str(tool_args.get("message") or "").strip()
        if not message:
            raise ValueError("mind.plan requires arguments.message")
        await mind.calling(message=message, mode="plan", metadata=metadata)
        mode = "plan"
    elif tool_name == "mind.batch":
        code = tool_args.get("code")
        if isinstance(code, str):
            code_list = [code]
        elif isinstance(code, list):
            code_list = [str(item) for item in code if str(item).strip()]
        else:
            code_list = []

        if not code_list:
            raise ValueError("mind.batch requires arguments.code")

        mode = _resolve_batch_mode(tool_args.get("mode"))
        await mind.mind_pack(code_list, mode, metadata=metadata)
    else:
        raise ValueError(f"unsupported mind mode tool: {tool_name}")

    atlas = f"{const.ATLAS_URL}?mode={mode}&cid={metadata['cid']}&sid={metadata['sid']}"
    return {
        "mode": mode,
        "cid": metadata["cid"],
        "sid": metadata["sid"],
        "atlas_url": atlas,
        "ok": True,
    }


def _resolve_batch_mode(value: typing.Any) -> typing.Literal["chat", "fast", "plan"]:
    """Normalize batch mode values to the supported literal set."""
    mode = str(value or "plan").strip().lower()
    if mode not in {"chat", "fast", "plan"}:
        raise ValueError("mind.batch arguments.mode must be chat, fast, or plan")
    return mode


def _build_tool_call_schema(config: AgentResidentConfig, device_id: str) -> dict[str, typing.Any]:
    """Build a ready-to-use remote tool-call schema for the current resident agent."""
    return {
        "selector": {
            "agent_id": config.agent_id,
            "device_id": device_id,
            "session_strategy": "latest_seen",
            "online_only": True,
        },
        "cid": "cid_demo",
        "sid": "sid_demo",
        "call_id": "call_refresh_001",
        "name": "refresh",
        "arguments": {
            "ttl_sec": 1,
        },
        "timeout_sec": 60,
    }


def _extract_http_error_detail(exc: httpx.HTTPStatusError) -> dict[str, typing.Any]:
    """Extract structured business error detail from an HTTP error response."""
    try:
        payload = exc.response.json()
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}

    for key in ("detail", "details", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    return payload


def _log_http_error_detail(prefix: str, exc: httpx.HTTPStatusError) -> None:
    """Log detail.code / detail.message / detail.extra for diagnosis."""
    detail = _extract_http_error_detail(exc)
    code = detail.get("code") if isinstance(detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else None
    extra = detail.get("extra") if isinstance(detail, dict) else None
    extra_text = json.dumps(extra, ensure_ascii=False) if extra is not None else ""

    logger.warning(
        f"{prefix} status={exc.response.status_code} "
        f"code={code or ''} message={message or ''} extra={extra_text}"
    )


def _update_last_acked_seq(runtime: AgentSessionRuntime, message: dict[str, typing.Any]) -> None:
    """Track the latest outbound sequence observed from the server."""
    seq = message.get("seq")
    if isinstance(seq, int) and seq > runtime.last_acked_seq:
        runtime.last_acked_seq = seq


async def _execute_tool_call(
    mind: "Mind",
    session: ClientSession,
    client: AgentClient,
    connection: typing.Any,
    runtime: AgentSessionRuntime,
    message: dict[str, typing.Any],
) -> None:
    """Execute one tool.call message and return tool.result."""
    payload_raw = message.get("payload")
    payload = payload_raw if isinstance(payload_raw, dict) else {}
    tool_name_raw = payload.get("name")
    tool_name = tool_name_raw if isinstance(tool_name_raw, str) else ""
    tool_args_raw = payload.get("arguments")
    tool_args = tool_args_raw if isinstance(tool_args_raw, dict) else {}
    cid_raw = message.get("cid")
    cid = cid_raw if isinstance(cid_raw, str) else None
    sid_raw = message.get("sid")
    sid = sid_raw if isinstance(sid_raw, str) else None
    message_id_raw = message.get("message_id")
    message_id = message_id_raw if isinstance(message_id_raw, str) else ""
    call_id_raw = payload.get("call_id")
    call_id = call_id_raw if isinstance(call_id_raw, str) else ""

    await client.send_ack(
        connection,
        session_id=runtime.session_id,
        acked_message_id=message_id,
    )
    logger.info(f"[Agent] ack sent tool={tool_name}")

    try:
        if tool_name in {"mind.chat", "mind.fast", "mind.plan", "mind.batch"}:
            result_payload = await _run_mind_mode(
                mind,
                tool_name=tool_name,
                tool_args=tool_args,
                cid=cid,
                sid=sid,
            )
            ok = True
            encoded = result_payload
        else:
            tool_result = await session.call_tool(tool_name, tool_args)
            ok = not tool_result.isError
            encoded = _serialize_tool_result(tool_result)

        await client.send_tool_result(
            connection,
            session_id=runtime.session_id,
            cid=cid or "cid_agent",
            sid=sid or "sid_agent",
            call_id=call_id,
            name=tool_name,
            ok=ok,
            result=encoded if ok else None,
            error=encoded if not ok else None,
        )
        logger.info(f"[Agent] tool.result sent tool={tool_name} ok={ok}")
    except Exception as exc:
        await client.send_tool_result(
            connection,
            session_id=runtime.session_id,
            cid=cid or "cid_agent",
            sid=sid or "sid_agent",
            call_id=call_id,
            name=tool_name,
            ok=False,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        logger.error(f"[Agent] tool call failed tool={tool_name}: {type(exc).__name__}: {exc}")


async def _handle_server_message(
    mind: "Mind",
    session: ClientSession,
    client: AgentClient,
    connection: typing.Any,
    runtime: AgentSessionRuntime,
    config: AgentResidentConfig,
    message: dict[str, typing.Any],
) -> None:
    """Handle one server message according to the resident protocol."""
    _update_last_acked_seq(runtime, message)

    message_type = str(message.get("type") or "")

    if message_type == "ready":
        if not runtime.hello_sent:
            await client.send_hello(
                connection,
                session_id=runtime.session_id,
                device_id=runtime.device_id,
                client_version=runtime.client_version,
            )
            runtime.hello_sent = True
            logger.info("[Agent] hello sent")

            if config.auto_event and not runtime.auto_event_sent:
                await client.send_event(
                    connection,
                    session_id=runtime.session_id,
                    cid="cid_agent",
                    sid="sid_agent",
                    mode=config.event_mode,
                    event=config.event or {"type": "text.delta", "text": "hello from resident mode"},
                )
                runtime.auto_event_sent = True
                logger.info("[Agent] event.push sent")
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
                    session,
                    client,
                    connection,
                    runtime,
                    config,
                    replay_message,
                )
        return None

    if message_type == "tool.call":
        await _execute_tool_call(mind, session, client, connection, runtime, message)
        return None

    if message_type == "ack":
        return None

    if message_type == "error":
        payload_raw = message.get("payload")
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        logger.warning(f"[Agent] server error: {json.dumps(payload, ensure_ascii=False)}")
        return None

    if message_type == "pong":
        return None


async def _connect_once(
    mind: "Mind",
    session: ClientSession,
    client: AgentClient,
    runtime: AgentSessionRuntime,
    config: AgentResidentConfig,
) -> None:
    """Establish one WS connection lifecycle and process messages until disconnect."""
    async with await client.connect_ws(
        session_id=runtime.session_id,
        ws_token=runtime.ws_token,
        ws_base_url=runtime.ws_url,
    ) as connection:
        if runtime.last_acked_seq > 0:
            await client.send_resume(
                connection,
                session_id=runtime.session_id,
                last_acked_seq=runtime.last_acked_seq,
            )
            logger.info(f"[Agent] resume sent last_acked_seq={runtime.last_acked_seq}")

        while True:
            message = await client.recv_json(connection)
            logger.info(f"[Agent] recv={json.dumps(message, ensure_ascii=False)}")
            await _handle_server_message(mind, session, client, connection, runtime, config, message)


async def _resume_or_reopen(
    client: AgentClient,
    runtime: AgentSessionRuntime,
    config: AgentResidentConfig,
) -> AgentSessionRuntime:
    """Try resume first; if the server says not resumable, reopen a fresh session."""
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
    )
    logger.info(f"[Agent] reopened session_id={session_id} device_id={device_id}")
    logger.info(f"[Agent] mind token={access_token or ''}")
    return reopened


async def agent_loop(
    mind: "Mind",
    session: ClientSession,
    config: AgentResidentConfig,
) -> None:
    """驻留模式主循环：创建会话、建立 WS，并持续处理心跳和工具调用。"""
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
        client_version=config.client_version,
    )

    logger.info(f"[Agent] connected target={config.base_url}")
    logger.info(f"[Agent] session_id={session_id} agent_id={config.agent_id} device_id={device_id}")
    logger.info(f"[Agent] mind token={access_token or ''}")
    logger.info("[Agent] tool-call schema:")
    logger.info(json.dumps(_build_tool_call_schema(config, device_id), ensure_ascii=False, indent=2))

    while not mind.task_event.is_set():
        try:
            await _connect_once(mind, session, client, runtime, config)
            return None
        except (ConnectionClosed, OSError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.warning(f"[Agent] disconnected: {type(exc).__name__}: {exc}")

            if not runtime.resume_token:
                logger.warning("[Agent] resume skipped: resume_token missing")
                await asyncio.sleep(2.0)
                continue

            try:
                runtime = await _resume_or_reopen(client, runtime, config)
            except (OSError, httpx.HTTPError, asyncio.TimeoutError) as resume_exc:
                logger.warning(f"[Agent] resume failed: {type(resume_exc).__name__}: {resume_exc}")
                await asyncio.sleep(2.0)
                continue
            except Exception as resume_exc:
                logger.error(f"[Agent] resume crashed: {type(resume_exc).__name__}: {resume_exc}")
                await asyncio.sleep(2.0)
                continue

            await asyncio.sleep(1.0)


def build_agent_config() -> AgentResidentConfig:
    """从命令行解析结果构建驻留模式配置。"""
    return AgentResidentConfig(
        base_url=const.DOMAIN.strip(),
        device_id="dev_win_001",
        agent_id="helix",
        client_version="1.2.3",
        platform=(platform.system().lower() or "windows").strip(),
        arch=(platform.machine().lower() or "amd64").strip(),
        auto_event=False,
        event_mode="chat",
        event=None,
    )


async def run_agent_mode(mind: "Mind") -> None:
    """命令行驻留模式入口。"""
    config = build_agent_config()
    model_api = mind.pref.to_config()

    async def function(
        session: ClientSession,
        _openai_tools: list[dict[str, typing.Any]],
        _tool_meta: dict[str, dict[str, typing.Any]],
    ) -> None:
        await agent_loop(mind, session, config)

    await mind.with_mcp_session(model_api, function)


if __name__ == "__main__":
    pass
