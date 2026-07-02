# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from loguru import logger
from backend.mcp_hub.hub_nexus.check import CheckService
from backend.mcp_hub.hub_nexus.infra.media import MediaService
from backend.mcp_hub.hub_nexus.infra.pack_builder import PackBuilder
from backend.utilities.trace import (
    summarize_request_target, summarize_result_failure
)


class ExecutorResultService(object):
    """负责执行结果收尾，包括媒体收集、pack 组装与检查收口。"""

    @staticmethod
    async def collect_media(
        *,
        source_kind: str,
        source: typing.Any,
        tool: str,
        timeout: float,
        media_index: typing.Optional[int] = None,
        media_path: typing.Optional[str] = None,
        content_type: typing.Optional[str] = None,
        step_artifact_dir: typing.Optional[str] = None
    ) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]], list[str]]:
        """按统一入口收集媒体，并返回 media、attachments 与日志。"""
        return await MediaService.collect_media(
            source_kind=source_kind,
            source=source,
            media_index=media_index,
            media_path=media_path,
            content_type=content_type,
            tool=tool,
            step_artifact_dir=step_artifact_dir,
            timeout=timeout
        )

    @staticmethod
    def finalize_pack(
        *,
        text: str,
        ok: bool,
        request: dict[str, typing.Any],
        response: dict[str, typing.Any],
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        logs: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None,
        extra_data: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """组装基础 pack，并继续执行 extract/asserts 收口。"""
        pack = PackBuilder.build_pack(
            text=text,
            ok=ok,
            request=request,
            response=response,
            attachments=attachments,
            logs=list(logs or []),
            error=error,
            extra_data=extra_data
        )

        checked        = CheckService.finalize_pack(pack, extract=extract, asserts=asserts)
        data           = checked.get("data") or {}
        request_target = summarize_request_target(data.get("request"))
        failure        = summarize_result_failure(data)
        level          = logger.debug if bool(checked.get("ok")) else logger.warning

        level(
            f"result finalize ok={checked.get('ok')} target={request_target} "
            f"status={(data.get('response') or {}).get('status')} "
            f"extract={len(data.get('extract') or {})} "
            f"assert_fail={(data.get('assert_summary') or {}).get('fail', 0)} "
            f"attachments={len(checked.get('attachments') or [])} logs={len(checked.get('logs') or [])} "
            f"failure={failure}"
        )
        return checked

    @staticmethod
    def finalize_http_like(
        *,
        text: str,
        ok: bool,
        request: dict[str, typing.Any],
        status: typing.Optional[int],
        headers: typing.Optional[dict[str, typing.Any]],
        elapsed_ms: int,
        body_text: typing.Optional[str],
        body_json: typing.Any,
        content_type: typing.Optional[str],
        content_length: typing.Optional[int],
        media: typing.Optional[list[dict[str, typing.Any]]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        logs: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None,
        extra_data: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """按 HTTP 类响应模板完成 pack 收尾。"""
        return ExecutorResultService.finalize_pack(
            text=text,
            ok=ok,
            request=request,
            response=PackBuilder.build_response_http_like(
                status=status,
                headers=headers,
                elapsed_ms=elapsed_ms,
                body_text=body_text,
                body_json=body_json,
                content_type=content_type,
                content_length=content_length,
                media=media
            ),
            extract=extract,
            asserts=asserts,
            attachments=attachments,
            logs=logs,
            error=error,
            extra_data=extra_data
        )

    @staticmethod
    def finalize_sse(
        *,
        text: str,
        ok: bool,
        request: dict[str, typing.Any],
        status: typing.Optional[int],
        headers: typing.Optional[dict[str, typing.Any]],
        elapsed_ms: int,
        events: typing.Optional[list[dict[str, typing.Any]]] = None,
        content_type: typing.Optional[str] = None,
        content_length: typing.Optional[int] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        logs: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """按 SSE 响应模板完成 pack 收尾。"""
        return ExecutorResultService.finalize_pack(
            text=text,
            ok=ok,
            request=request,
            response=PackBuilder.build_response_sse(
                status=status,
                headers=headers,
                elapsed_ms=elapsed_ms,
                events=events,
                content_type=content_type,
                content_length=content_length,
                media=media
            ),
            extract=extract,
            asserts=asserts,
            attachments=attachments,
            logs=logs,
            error=error
        )

    @staticmethod
    def finalize_ws(
        *,
        text: str,
        ok: bool,
        request: dict[str, typing.Any],
        elapsed_ms: int,
        messages: typing.Optional[list[str]] = None,
        response_error: typing.Optional[str] = None,
        media: typing.Optional[list[dict[str, typing.Any]]] = None,
        extract: typing.Optional[dict[str, str]] = None,
        asserts: typing.Optional[list[dict[str, typing.Any]]] = None,
        attachments: typing.Optional[list[dict[str, typing.Any]]] = None,
        logs: typing.Optional[list[str]] = None,
        error: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """按 WebSocket 响应模板完成 pack 收尾。"""
        return ExecutorResultService.finalize_pack(
            text=text,
            ok=ok,
            request=request,
            response=PackBuilder.build_response_ws(
                elapsed_ms=elapsed_ms,
                messages=messages,
                error=response_error,
                media=media
            ),
            extract=extract,
            asserts=asserts,
            attachments=attachments,
            logs=logs,
            error=error
        )


if __name__ == '__main__':
    pass
