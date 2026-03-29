# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
import time
import typing
import asyncio
import websockets
from backend.mcp_hub.hub_nexus.infra.pack_builder import PackBuilder
from backend.mcp_hub.hub_nexus.infra.result import ExecutorResultService
from backend.mcp_hub.hub_nexus.infra.core import (
    ClockService, UrlService
)
from backend.utilities import const


class WsExecutor(object):

    @staticmethod
    async def execute(
        *,
        url: str,
        headers: typing.Optional[dict[str, str]] = None,
        sends: typing.Optional[list[str]] = None,
        timeout: float = 60.0,
        max_messages: int = 10,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_index: typing.Optional[int] = None,
        media_path: typing.Optional[str] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行单次 WebSocket 会话，并采集发送与接收消息。"""
        t0 = time.perf_counter()

        headers = headers or {}
        sends = sends or []
        recv: list[str] = []
        last_err: typing.Optional[str] = None

        try:
            connect_kwargs: dict[str, typing.Any] = {
                "additional_headers": headers,
                "open_timeout": timeout
            }
            if UrlService.is_loopback(url):
                connect_kwargs["proxy"] = None

            async with websockets.connect(url, **connect_kwargs) as ws:
                for item in sends:
                    await ws.send(item)

                for _ in range(int(max_messages)):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except websockets.exceptions.ConnectionClosedOK:
                        break
                    recv.append(msg if isinstance(msg, str) else msg.decode(const.CHARSET, const.IGNORE))
        except (
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.WebSocketException,
            OSError,
            asyncio.TimeoutError
        ) as e:
            last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ClockService.ms_since(t0)
        ok = (last_err is None) or bool(recv)

        msg_target: list[typing.Any] = []
        for item in recv:
            try:
                msg_target.append(json.loads(item))
            except (TypeError, ValueError, json.JSONDecodeError):
                msg_target.append(item)

        media_list, attachments, media_logs = await ExecutorResultService.collect_media(
            source_kind="ws_messages",
            source=msg_target,
            media_index=media_index,
            media_path=media_path,
            tool="ws_media",
            step_artifact_dir=step_artifact_dir,
            timeout=timeout
        )

        request_data = PackBuilder.build_request_ws(
            url=url,
            headers=headers,
            sends=sends,
            timeout=timeout,
            max_messages=max_messages
        )

        return ExecutorResultService.finalize_ws(
            text=f"WS {url} msgs={len(recv)} ({elapsed_ms}ms)",
            ok=ok,
            request=request_data,
            elapsed_ms=elapsed_ms,
            messages=recv,
            response_error=None if ok else last_err,
            media=media_list,
            extract=extract,
            asserts=asserts,
            attachments=attachments,
            logs=media_logs,
            error=last_err
        )


if __name__ == '__main__':
    pass
