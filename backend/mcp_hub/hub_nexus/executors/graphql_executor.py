# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
import time
import httpx
import typing
from loguru import logger
from backend.mcp_hub.hub_nexus.infra.pack_builder import PackBuilder
from backend.mcp_hub.hub_nexus.infra.media import MediaService
from backend.mcp_hub.hub_nexus.infra.result import ExecutorResultService
from backend.mcp_hub.hub_nexus.infra.core import (
    ClockService, UrlService
)
from backend.utilities.trace import summarize_args


class GraphqlExecutor(object):

    @staticmethod
    async def execute(
        *,
        url: str,
        query: str,
        variables: typing.Optional[dict[str, typing.Any]] = None,
        operation_name: typing.Optional[str] = None,
        base_url: typing.Optional[str] = None,
        headers: typing.Optional[dict[str, str]] = None,
        params: typing.Optional[dict[str, typing.Any]] = None,
        timeout: float = 30.0,
        retries: int = 0,
        follow_redirects: bool = True,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        media_path: typing.Optional[str] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """执行单次 GraphQL 请求，并附带 GraphQL 专属调试信息。"""
        gql_method  = "POST"
        gql_url     = UrlService.join(base_url, url)
        gql_headers = dict(headers or {})
        gql_headers.setdefault("Content-Type", "application/json")

        gql_payload: dict[str, typing.Any] = {
            "query"     : query,
            "variables" : variables or {}
        }
        if operation_name:
            gql_payload["operationName"] = operation_name

        t0 = time.perf_counter()
        last_err: typing.Optional[str] = None

        body_text_view: typing.Optional[str] = None
        body_json: typing.Any = None
        body_bytes: bytes = b""
        gql_errors: typing.Any = None

        status: typing.Optional[int] = None
        resp_headers: dict[str, typing.Any] = {}
        resp_ct: typing.Optional[str] = None
        media_list: list[dict[str, typing.Any]] = []
        attachments: list[dict[str, typing.Any]] = []
        media_logs: list[str] = []
        ok = False

        request_data = PackBuilder.build_request_http_like(
            method=gql_method,
            url=gql_url,
            headers=gql_headers,
            params=params,
            json_body=gql_payload,
            body_text=None,
            timeout=timeout,
            retries=retries,
            follow_redirects=follow_redirects,
            form=None,
            files=None
        )
        logger.debug(
            f"graphql exec begin url={gql_url} timeout={timeout} retries={retries} "
            f"follow_redirects={follow_redirects} request={summarize_args(request_data)}"
        )

        async with httpx.AsyncClient(
            **UrlService.httpx_client_kwargs(
                url=gql_url,
                timeout=timeout,
                follow_redirects=follow_redirects
            )
        ) as client:
            for attempt in range(max(0, int(retries)) + 1):
                try:
                    logger.debug(f"graphql attempt url={gql_url} attempt={attempt + 1}")
                    resp = await client.request(
                        gql_method,
                        gql_url,
                        headers=gql_headers,
                        params=params,
                        json=gql_payload
                    )
                    elapsed_ms = ClockService.ms_since(t0)

                    status       = resp.status_code
                    resp_headers = dict(resp.headers)
                    resp_ct      = str(resp.headers.get("content-type") or "")
                    body_bytes   = resp.content

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
                        source_kind="json_body",
                        source=body_json,
                        media_path=media_path,
                        tool="graphql_media",
                        step_artifact_dir=step_artifact_dir,
                        timeout=timeout
                    )

                    http_ok = 200 <= int(status) < 400
                    gql_errors = body_json.get("errors") if isinstance(body_json, dict) else None
                    ok = bool(http_ok) and not bool(gql_errors)
                    level = logger.debug if ok else logger.warning
                    level(
                        f"graphql exec end url={gql_url} status={status} elapsed_ms={elapsed_ms} "
                        f"gql_errors={0 if not gql_errors else len(gql_errors)} media={len(media_list)}"
                    )

                    return ExecutorResultService.finalize_http_like(
                        text=(
                            f"GQL POST {gql_url} -> {status} ({elapsed_ms}ms)"
                            if ok else
                            f"GQL POST {gql_url} -> FAIL ({elapsed_ms}ms)"
                        ),
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
                        error=None,
                        extra_data=PackBuilder.build_gql_extra(
                            query=query,
                            variables=variables or {},
                            operation_name=operation_name,
                            errors=gql_errors
                        )
                    )
                except (httpx.TimeoutException, httpx.RequestError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"
                    logger.warning(
                        f"graphql attempt error url={gql_url} attempt={attempt + 1} error={last_err}"
                    )

        elapsed_ms = ClockService.ms_since(t0)
        logger.error(f"graphql exec failed url={gql_url} elapsed_ms={elapsed_ms} error={last_err}")
        return ExecutorResultService.finalize_http_like(
            text=f"GQL POST {gql_url} -> ERROR ({elapsed_ms}ms) {last_err}",
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
            error=last_err,
            extra_data=PackBuilder.build_gql_extra(
                query=query,
                variables=variables or {},
                operation_name=operation_name,
                errors=gql_errors
            )
        )


if __name__ == '__main__':
    pass
