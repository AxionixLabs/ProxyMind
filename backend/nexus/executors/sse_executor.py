#  ____ ____  _____   _____                _
# / ___/ ___|| ____| | ____|_  _____ _   _| |_ ___  _ __
# \___ \___ \|  _|   |  _| \ \/ / __| | | | __/ _ \| '__|
#  ___) |__) | |___  | |___ >  < (__| |_| | || (_) | |
# |____/____/|_____| |_____/_/\_\___|\__,_|\__\___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import httpx
import typing
from backend.nexus.check_service import CheckService
from backend.nexus.infra.pack_builder import PackBuilder
from backend.nexus.infra.media_service import MediaService
from backend.nexus.infra.core_helpers import (
    ClockService, UrlService
)
from backend.nexus.infra.file_payload_service import FilePayloadService
from backend.nexus.infra.sse_parser import SseParser


class SseExecutor(object):

    @staticmethod
    async def execute(
        *,
        method: str,
        url: str,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        json_body: typing.Optional[dict[str, typing.Any]] = None,
        body_text: typing.Optional[str] = None,
        form: typing.Optional[dict[str, typing.Any]] = None,
        files: typing.Optional[list[dict[str, typing.Any]]] = None,
        timeout: float = 60.0,
        retries: int = 0,
        follow_redirects: bool = True,
        max_events: typing.Optional[int] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_index: typing.Optional[int] = None,
        media_path: typing.Optional[str] = None,
        save_response: bool = False,
        save_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行单次 SSE 请求，并把事件流整理成统一结果。"""
        method  = (method or "GET").upper()
        url     = UrlService.join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None
        events: list[dict[str, typing.Any]] = []

        status: typing.Optional[int] = None
        resp_headers: dict[str, typing.Any] = {}
        resp_ct: typing.Optional[str] = None
        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        media_logs: list[str] = []
        ok = False

        request_data = PackBuilder.build_request_http_like(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json_body=json_body,
            body_text=body_text,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects,
            form=form,
            files=files
        )

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    files_payload = FilePayloadService.files_payload(files)
                    events = []
                    status = None

                    async with client.stream(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text,
                        data=form,
                        files=files_payload
                    ) as resp:
                        status       = resp.status_code
                        resp_headers = dict(resp.headers)
                        resp_ct      = str(resp.headers.get("content-type") or "")
                        elapsed_ms   = ClockService.ms_since(t0)

                        if status != 200:
                            response_data = PackBuilder.build_response_sse(
                                status=status,
                                headers=resp_headers,
                                elapsed_ms=elapsed_ms,
                                events=events,
                                content_type=resp_ct,
                                content_length=None,
                                media=media_list
                            )
                            pack = PackBuilder.build_pack(
                                text=f"SSE {method} {url} -> {status} ({elapsed_ms}ms)",
                                ok=ok,
                                request=request_data,
                                response=response_data,
                                attachments=attachments,
                                logs=media_logs[:],
                                error=last_err
                            )
                            return CheckService.finalize_pack(pack, extract=extract, asserts=asserts)

                        buf = ""
                        async for chunk in resp.aiter_text():
                            buf += chunk
                            buf = buf.replace("\r\n", "\n")

                            while "\n\n" in buf:
                                raw, buf = buf.split("\n\n", 1)
                                ev = SseParser.parse_block(raw)
                                if not ev: continue

                                events.append({"event": ev.event, "id": ev.id, "data": ev.data})
                                if max_events and 0 < int(max_events) <= len(events):
                                    elapsed_ms = ClockService.ms_since(t0)
                                    ok = status == 200 and len(events) > 0
                                    media_list, attachments, media_logs = await MediaService.collect_media(
                                        source_kind="sse_events",
                                        source=events,
                                        media_index=media_index,
                                        media_path=media_path,
                                        save_response=save_response,
                                        save_dir=save_dir,
                                        tool="sse_media",
                                        timeout=timeout
                                    )
                                    response_data = PackBuilder.build_response_sse(
                                        status=status,
                                        headers=resp_headers,
                                        elapsed_ms=elapsed_ms,
                                        events=events,
                                        content_type=resp_ct,
                                        content_length=None,
                                        media=media_list
                                    )
                                    pack = PackBuilder.build_pack(
                                        text=f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                                        ok=ok,
                                        request=request_data,
                                        response=response_data,
                                        attachments=attachments,
                                        logs=media_logs[:],
                                        error=last_err
                                    )
                                    return CheckService.finalize_pack(pack, extract=extract, asserts=asserts)

                        tail = SseParser.parse_block(buf)
                        if tail:
                            events.append({"event": tail.event, "id": tail.id, "data": tail.data})

                    elapsed_ms = ClockService.ms_since(t0)
                    ok = status == 200 and len(events) > 0
                    media_list, attachments, media_logs = await MediaService.collect_media(
                        source_kind="sse_events",
                        source=events,
                        media_index=media_index,
                        media_path=media_path,
                        save_response=save_response,
                        save_dir=save_dir,
                        tool="sse_media",
                        timeout=timeout
                    )
                    response_data = PackBuilder.build_response_sse(
                        status=status,
                        headers=resp_headers,
                        elapsed_ms=elapsed_ms,
                        events=events,
                        content_type=resp_ct,
                        content_length=None,
                        media=media_list
                    )
                    pack = PackBuilder.build_pack(
                        text=f"SSE {method} {url} events={len(events)} ({elapsed_ms}ms)",
                        ok=ok,
                        request=request_data,
                        response=response_data,
                        attachments=attachments,
                        logs=media_logs[:],
                        error=last_err
                    )
                    return CheckService.finalize_pack(pack, extract=extract, asserts=asserts)
                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ClockService.ms_since(t0)
        response_data = PackBuilder.build_response_sse(
            status=status,
            headers=resp_headers,
            elapsed_ms=elapsed_ms,
            events=events,
            content_type=resp_ct,
            content_length=None,
            media=media_list
        )
        pack = PackBuilder.build_pack(
            text=f"SSE {method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            ok=ok,
            request=request_data,
            response=response_data,
            attachments=attachments,
            logs=media_logs[:],
            error=last_err
        )
        return CheckService.finalize_pack(pack, extract=extract, asserts=asserts)


if __name__ == '__main__':
    pass
