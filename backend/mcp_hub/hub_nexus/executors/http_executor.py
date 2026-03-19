#  _   _ _   _           _____                _
# | | | | |_| |_ _ __   | ____|_  _____ _   _| |_ ___  _ __
# | |_| | __| __| '_ \  |  _| \ \/ / __| | | | __/ _ \| '__|
# |  _  | |_| |_| |_) | | |___ >  < (__| |_| | || (_) | |
# |_| |_|\__|\__| .__/  |_____/_/\_\___|\__,_|\__\___/|_|
#               |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
import time
import httpx
import typing
from backend.mcp_hub.hub_nexus.infra.pack_builder import PackBuilder
from backend.mcp_hub.hub_nexus.infra.media import MediaService
from backend.mcp_hub.hub_nexus.infra.result import ExecutorResultService
from backend.mcp_hub.hub_nexus.infra.core import (
    ClockService, UrlService
)
from backend.mcp_hub.hub_nexus.infra.file_payload import FilePayloadService


class HttpExecutor(object):

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
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行单次 HTTP 请求，并返回统一格式的执行结果。"""
        method  = (method or "GET").upper()
        url     = UrlService.join(base_url, url)
        headers = dict(headers or {})

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        body_text_view: typing.Optional[str] = None
        body_json: typing.Any = None
        body_bytes: bytes = b""

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

        async with httpx.AsyncClient(
            **UrlService.httpx_client_kwargs(
                url=url,
                timeout=timeout,
                follow_redirects=follow_redirects
            )
        ) as client:
            for _ in range(max(0, int(retries)) + 1):
                try:
                    files_payload = FilePayloadService.files_payload(files)

                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                        content=body_text,
                        data=form,
                        files=files_payload
                    )
                    elapsed_ms = ClockService.ms_since(t0)

                    status = resp.status_code
                    resp_headers = dict(resp.headers)
                    resp_ct = str(resp.headers.get("content-type") or "")
                    body_bytes = resp.content

                    try:
                        body_json = resp.json()
                    except (TypeError, ValueError, json.JSONDecodeError):
                        body_json = None

                    try:
                        body_text_view = resp.text
                    except (TypeError, ValueError, AttributeError):
                        body_text_view = None

                    if MediaService.detect_media_kind(resp_ct):
                        body_json = None
                        body_text_view = None

                    media_list, attachments, media_logs = await ExecutorResultService.collect_media(
                        source_kind="http_body",
                        source=body_bytes,
                        content_type=resp_ct,
                        tool="http_media",
                        step_artifact_dir=step_artifact_dir,
                        timeout=timeout
                    )

                    ok = 200 <= int(resp.status_code) < 400
                    return ExecutorResultService.finalize_http_like(
                        text=f"{method} {url} -> {resp.status_code} ({elapsed_ms}ms)",
                        ok=ok,
                        request=request_data,
                        status=status,
                        headers=resp_headers,
                        elapsed_ms=elapsed_ms,
                        body_text=body_text_view,
                        body_json=body_json,
                        content_type=resp_ct,
                        content_length=len(body_bytes),
                        media=media_list,
                        extract=extract,
                        asserts=asserts,
                        attachments=attachments,
                        logs=media_logs,
                        error=last_err
                    )
                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"

        elapsed_ms = ClockService.ms_since(t0)
        return ExecutorResultService.finalize_http_like(
            text=f"{method} {url} -> ERROR ({elapsed_ms}ms) {last_err}",
            ok=ok,
            request=request_data,
            status=status,
            headers=resp_headers,
            elapsed_ms=elapsed_ms,
            body_text=body_text_view,
            body_json=body_json,
            content_type=resp_ct,
            content_length=len(body_bytes),
            media=media_list,
            extract=extract,
            asserts=asserts,
            attachments=attachments,
            logs=media_logs,
            error=last_err
        )


if __name__ == '__main__':
    pass
