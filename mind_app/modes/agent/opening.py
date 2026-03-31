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
from loguru import logger
from ...runtime.agent_client import AgentClient
from .models import AgentConfig
from mind_nova import const


def build_device_id() -> str:
    """为当前设备生成稳定的订阅设备标识。"""
    raw = "|".join(
        [
            socket.gethostname().strip().lower(),
            hex(uuid.getnode()),
            platform.system().strip().lower(),
            platform.machine().strip().lower()
        ]
    )
    digest = hashlib.sha1(raw.encode(const.CHARSET, errors="ignore")).hexdigest()[:16]
    return f"dev_{digest}"


def normalize_open_payload(
    client: AgentClient,
    opened: dict[str, typing.Any]
) -> tuple[str, str, str | None, str | None, str | None]:
    """从 `/agents/open` 响应中提取会话与握手信息。"""
    data = client.unwrap_data(opened)

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


async def open_with_fallback(
    client: AgentClient,
    config: AgentConfig
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
        log_http_error_detail("[Agent] open conflict", exc)
        raise


async def open_runtime(
    client: AgentClient,
    config: AgentConfig
) -> tuple[dict[str, typing.Any], str] | None:
    """持续重试 open，直到成功创建会话。"""
    while True:
        try:
            return await open_with_fallback(client, config)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                logger.debug(
                    "[Agent] open conflict persists; "
                    "the server may still hold another session for this agent. retrying in 5s"
                )
                await asyncio.sleep(5.0)
                continue
            raise
        except (OSError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.debug(
                f"[Agent] open failed: {type(exc).__name__}: {exc}. retrying in 5s"
            )
            await asyncio.sleep(5.0)
            continue


def extract_http_error_detail(exc: httpx.HTTPStatusError) -> dict[str, typing.Any]:
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


def log_http_error_detail(prefix: str, exc: httpx.HTTPStatusError) -> None:
    """记录 detail.code / detail.message / detail.extra 以便排查问题。"""
    detail = extract_http_error_detail(exc)

    code    = detail.get("code") if isinstance(detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else None
    extra   = detail.get("extra") if isinstance(detail, dict) else None

    extra_text = json.dumps(extra, ensure_ascii=False) if extra is not None else ""

    logger.debug(
        f"{prefix} status={exc.response.status_code} "
        f"code={code or ''} message={message or ''} extra={extra_text}"
    )


if __name__ == '__main__':
    pass
