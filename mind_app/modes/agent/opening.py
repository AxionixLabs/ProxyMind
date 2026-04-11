# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ssl
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


def iter_exception_chain(exc: BaseException) -> typing.Iterator[BaseException]:
    """按因果链展开异常，便于识别被包装过的 TLS 错误。"""
    stack = [exc]
    seen: set[int] = set()

    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        yield current

        for nested in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(nested, BaseException):
                stack.append(nested)


def is_tls_certificate_error(exc: BaseException) -> bool:
    """识别证书校验失败，避免把不可恢复问题当成普通网络抖动重试。"""
    patterns = (
        "certificate verify failed",
        "certificate_verify_failed",
        "self-signed certificate",
        "self signed certificate",
        "unable to get local issuer certificate",
    )

    for current in iter_exception_chain(exc):
        if isinstance(current, ssl.SSLCertVerificationError):
            return True

        message = f"{type(current).__name__}: {current}".lower()
        if isinstance(current, ssl.SSLError) and any(pattern in message for pattern in patterns):
            return True
        if any(pattern in message for pattern in patterns):
            return True

    return False


def summarize_tls_certificate_error(exc: BaseException) -> str:
    """返回证书校验失败链路中最有用的一段摘要。"""
    for current in iter_exception_chain(exc):
        message = f"{type(current).__name__}: {current}".strip()
        lowered = message.lower()
        if (
            isinstance(current, ssl.SSLCertVerificationError)
            or "certificate verify failed" in lowered
            or "certificate_verify_failed" in lowered
            or "self-signed certificate" in lowered
            or "self signed certificate" in lowered
            or "unable to get local issuer certificate" in lowered
        ):
            return message

    return f"{type(exc).__name__}: {exc}"


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
) -> tuple[str, str, str | None, str | None, str | None, dict[str, typing.Any] | None]:
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

    credential_raw   = data.get("credential")
    credential_data  = credential_raw if isinstance(credential_raw, dict) else {}
    credential_token = credential_data.get("token")
    credential       = credential_token if isinstance(credential_token, str) else None

    examples_raw       = data.get("examples")
    examples           = examples_raw if isinstance(examples_raw, dict) else {}
    mind_call_raw      = examples.get("mind_call")
    mind_call_example  = mind_call_raw if isinstance(mind_call_raw, dict) else None

    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("agent open response missing session_id or ws_token")
    if not isinstance(ws_token, str) or not ws_token:
        raise RuntimeError("agent open response missing session_id or ws_token")

    return session_id, ws_token, ws_url, resume_token, credential, mind_call_example


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
            if is_tls_certificate_error(exc):
                logger.debug(
                    "[Agent] open failed: non-retriable tls error "
                    f"{summarize_tls_certificate_error(exc)}"
                )
                raise
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
