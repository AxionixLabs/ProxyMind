# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import json
import time
import uuid
import httpx
import typing
import platform
import mimetypes
from pathlib import Path
from loguru import logger
from engine.channel import Channel
from mind_app.stream_ui import StreamUI
from mind_nova import const

UploadProgressCallback = typing.Callable[[dict[str, typing.Any]], typing.Awaitable[None]]
DEFAULT_UPLOAD_CHUNK_SIZE: int = 64 * 1024


async def cap_request(req: httpx.Request) -> None:
    """记录请求摘要，便于排查鉴权和链路问题。"""
    a = req.headers.get("Authorization", "")
    logger.debug(
        f"[MCP] {req.method} {req.url} auth={'OK' if a.startswith('Bearer ') else 'MISSING'}"
    )


async def cap_response(response: httpx.Response) -> None:
    """捕获失败响应体，便于后续调试。"""
    if response.status_code >= 400:
        try:
            response.extensions["error_body"] = await response.aread()
        except Exception as e:
            _ = e
            response.extensions["error_body"] = b""


async def fetch_manifest() -> typing.Optional[dict[str, typing.Any]]:
    """获取当前平台对应的清单配置。"""
    headers = Channel.make_headers()
    params  = Channel.make_params() | {
        "station" : sys.platform,
        "arch"    : platform.machine()
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.request("GET", const.MANIFEST_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

    except Exception as e:
        return logger.debug(f"[Manifest] fetch failed: {type(e).__name__}: {e}")

    if not isinstance(data, dict) or not data.get("ok"):
        return None

    return data.get("data")


async def streaming(
    url: str,
    headers: dict,
    payload: dict,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """按 SSE `data:` 行读取并解析事件流。"""
    async with httpx.AsyncClient(timeout=timeout, event_hooks={"response": [cap_response]}) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue

                try:
                    event = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue

                yield event


async def post_stream_event(
    mode: typing.Literal["chat", "fast", "plan"],
    cid: str,
    sid: str,
    event: dict[str, typing.Any],
    *,
    timeout: float = 30.0
) -> None:
    """事件上报：把一条事件写入服务端缓存并广播给 SSE 订阅者。"""
    headers = Channel.make_headers()
    payload = {
        "mode"  : mode,
        "cid"   : cid,
        "sid"   : sid,
        "event" : event
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(const.STREAM_EVENT_URL, headers=headers, json=payload)
        r.raise_for_status()


async def open_report_session(
    mode: typing.Literal["chat", "fast", "plan"],
    cid: str,
    sid: str,
    *,
    proto: str | None = None,
    timeout: float = 10.0
) -> dict[str, typing.Any]:
    """打开服务端报告会话，返回 report_url / report_id / stream_url / replay_url。"""
    headers = Channel.make_headers()
    payload: dict[str, typing.Any] = {
        "mode" : mode,
        "cid"  : cid,
        "sid"  : sid
    }
    if isinstance(proto, str) and proto.strip():
        payload["proto"] = proto.strip()

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(const.REPORT_OPEN_URL, headers=headers, json=payload)
        r.raise_for_status()
        body = r.json()

    if not isinstance(body, dict):
        raise RuntimeError("reports/open returned non-object payload")
    if not body.get("ok"):
        raise RuntimeError(f"reports/open rejected payload: {body}")

    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("reports/open missing data object")

    return data


async def upload_file_stream(
    path: str,
    agent_id: str,
    prefix: str = "uploads",
    timeout: float = 60.0,
    progress_callback: typing.Optional[UploadProgressCallback] = None,
    chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE
) -> dict[str, typing.Any]:
    """流式上传本地文件到服务端。"""

    def upload_progress_payload(
        current_uploaded_bytes: int,
        current_total_bytes: int,
        progress_started_at: float,
        *,
        done: bool
    ) -> dict[str, typing.Any]:

        elapsed_sec = max(0.0, time.monotonic() - progress_started_at)
        speed = (float(current_uploaded_bytes) / elapsed_sec) if elapsed_sec > 0 else 0.0
        percent = 1.0 if current_total_bytes <= 0 and done else (
            min(1.0, float(current_uploaded_bytes) / float(current_total_bytes)) if current_total_bytes > 0 else 0.0
        )

        return {
            "uploaded_bytes"      : int(current_uploaded_bytes),
            "total_bytes"         : int(current_total_bytes),
            "percent"             : percent,
            "elapsed_sec"         : elapsed_sec,
            "speed_bytes_per_sec" : speed,
            "done"                : done
        }

    def multipart_field(
        part_boundary: str,
        name: str,
        value: str
    ) -> bytes:
        return (
            f"--{part_boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode(const.CHARSET)

    def multipart_file_header(
        part_boundary: str,
        name: str,
        filename: str,
        content_type: str
    ) -> bytes:
        return (
            f"--{part_boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode(const.CHARSET)

    def multipart_closing(
        part_boundary: str
    ) -> bytes:
        return f"\r\n--{part_boundary}--\r\n".encode(const.CHARSET)

    async def body() -> typing.AsyncGenerator[bytes, None]:
        yield field_agent
        yield field_prefix
        yield field_file

        with p.open("rb") as f:
            while True:
                chunk = f.read(max(1, int(chunk_size)))
                if not chunk:
                    break

                yield chunk
                upload_state["uploaded_bytes"] += len(chunk)

                if progress_callback is not None:
                    await progress_callback(
                        upload_progress_payload(
                            upload_state["uploaded_bytes"], file_size, started_at, done=False
                        )
                    )

        yield closing

    if not (p := Path(path).expanduser()).exists() or not p.is_file():
        raise RuntimeError(f"upload_file_stream: file not exists: {p}")

    headers = Channel.make_headers()
    headers.pop("Content-Type", None)

    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"

    boundary     = uuid.uuid4().hex
    field_agent  = multipart_field(boundary, "agent_id", agent_id)
    field_prefix = multipart_field(boundary, "prefix", prefix)
    field_file   = multipart_file_header(boundary, "file", p.name, ctype)
    closing      = multipart_closing(boundary)

    file_size = int(p.stat().st_size)

    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    headers["Content-Length"] = str(
        len(field_agent) + len(field_prefix) + len(field_file) + file_size + len(closing)
    )

    started_at = time.monotonic()
    upload_state = {"uploaded_bytes": 0}

    if progress_callback is not None:
        await progress_callback(
            upload_progress_payload(upload_state["uploaded_bytes"], file_size, started_at, done=False)
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(const.FILE_STREAM_URL, headers=headers, content=body())
        r.raise_for_status()

        if progress_callback is not None:
            await progress_callback(upload_progress_payload(file_size, file_size, started_at, done=True))

        return r.json()


async def post_tool_result(
    cid: str,
    sid: str,
    call_id: str,
    name: str,
    ok: bool,
    result: typing.Union[
        None,
        str,
        int,
        bool,
        float,
        list[typing.Any],
        dict[str, typing.Any]
    ]
) -> None:
    """把工具执行结果回传给服务端主循环。"""
    headers = Channel.make_headers()
    payload = {
        "cid"     : cid,
        "sid"     : sid,
        "call_id" : call_id,
        "name"    : name,
        "ok"      : ok,
        "result"  : result
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(const.TOOL_RESULT_URL, headers=headers, json=payload)
        r.raise_for_status()


async def stream_chat(
    mode: str,
    model_api: dict[str, typing.Any],
    message: str,
    openai_tools: list[dict],
    attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """流式获取 chat/fast 模式事件。"""
    headers = Channel.make_headers()
    payload = {
        "mode"     : mode,
        "llm_conf" : model_api,
        "message"  : message,
        "tools"    : openai_tools,
        **kwargs
    }
    if attachments:
        payload["attachments"] = attachments

    async for event in streaming(const.STREAM_CHAT_URL, headers, payload, timeout):
        event_type = str(event.get("type") or "")

        if event_type == "ping":
            continue

        yield event


async def stream_plan(
    mode: str,
    model_api: dict[str, typing.Any],
    message: str,
    openai_tools: list[dict],
    extras: typing.Optional[dict[str, typing.Any]] = None,
    timeout: float = 60.0,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """流式获取静态规划事件。"""
    headers = Channel.make_headers()
    payload = {
        "mode"     : mode,
        "llm_conf" : model_api,
        "message"  : message,
        "tools"    : openai_tools,
        "extras"   : extras,
        **kwargs
    }

    async for event in streaming(const.STREAM_PLAN_URL, headers, payload, timeout):
        event_type = str(event.get("type") or "")

        if event_type in ["plan.done", "ping"]:
            continue

        yield event


async def stream_heal(
    model_api: dict[str, typing.Any],
    page_id: str,
    station: str,
    locator: str,
    page_dump: str,
    screenshot_base64: str,
    wm_size: dict,
    timeout: float = 60.0,
    slog: typing.Optional[StreamUI] = None,
    *_,
    **kwargs
) -> typing.AsyncGenerator[dict, None]:
    """流式获取修复链路事件。"""
    headers = Channel.make_headers()
    payload = {
        "llm_conf"   : model_api,
        "app_id"     : const.APP_DESC,
        "page_id"    : page_id,
        "platform"   : station,
        "locator"    : locator,
        "page_dump"  : page_dump,
        "screenshot" : f"data:image/png;base64,{screenshot_base64}",
        "wm_size"    : wm_size,
        "context"    : kwargs
    }

    async for event in streaming(const.STREAM_HEAL_URL, headers, payload, timeout):
        match event.get("type"):
            case "ping":
                continue

            case "heal.step":
                message = str(event.get("message") or "")
                if not message:
                    continue
                if slog:
                    await slog.update_heal_status_summary(message)
                    await slog.feed(message, display=StreamUI.BLOCK)
                else:
                    logger.debug(message)
                continue

            case "heal.failed":
                error = str(event.get("error") or "unknown heal error")
                if slog:
                    await slog.update_heal_status_summary(error)
                    await slog.feed(error, display=StreamUI.BLOCK)
                else:
                    logger.debug(error)

        yield event


async def stream_rule(
    mode: str,
    model_api: dict[str, typing.Any],
    message: str,
    context: dict[str, typing.Any],
    metadata: dict[str, typing.Any],
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """流式获取规则执行事件。"""
    headers = Channel.make_headers()
    payload = {
        "mode"      : mode,
        "llm_conf"  : model_api,
        "message"   : message,
        "metadata"  : metadata,
        "extras"    : {"context" : context}
    }

    async for event in streaming(const.STREAM_RULE_URL, headers, payload, timeout):
        event_type = str(event.get("type") or "")

        if event_type in {"turn.thinking", "ping"}:
            continue

        yield event


if __name__ == '__main__':
    pass
