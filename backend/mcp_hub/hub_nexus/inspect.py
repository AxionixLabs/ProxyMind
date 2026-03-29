# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.models.model_nexus import (
    NexusKind, NexusRequest, NexusBatchRequest
)
from backend.mcp_hub.hub_nexus.domain.template import TemplateService


class NexusInspectionService(object):
    """Render and validate normalized nexus requests before execution."""

    @staticmethod
    def render_request(
        *,
        kind: NexusKind,
        request: NexusRequest,
        env: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """渲染单请求中的模板变量与共享默认值。"""
        ctx       = dict(request.template_vars or {})
        env_r     = TemplateService.render(dict(env or {}), ctx) if env else {}
        request_r = TemplateService.render(dict(request.request or {}), ctx)
        extract_r = TemplateService.render(request.extract, ctx) if request.extract else None
        asserts_r = TemplateService.render(request.asserts, ctx) if request.asserts else None

        return {
            "kind"          : kind,
            "name"          : request.name,
            "env"           : env_r,
            "request"       : request_r,
            "extract"       : extract_r,
            "asserts"       : asserts_r,
            "template_vars" : ctx
        }

    @staticmethod
    def render_batch(
        *,
        kind: NexusKind,
        batch: NexusBatchRequest,
    ) -> dict[str, typing.Any]:
        """渲染批量请求中的模板变量与共享默认值。"""
        ctx   = dict(batch.template_vars or {})
        env_r = TemplateService.render(dict(batch.env or {}), ctx) if batch.env else {}
        items = []

        for item in batch.items:
            request_r = TemplateService.render(dict(item.request or {}), ctx)
            extract_r = TemplateService.render(item.extract, ctx) if item.extract else None
            asserts_r = TemplateService.render(item.asserts, ctx) if item.asserts else None
            items.append(
                {
                    "name"    : item.name,
                    "request" : request_r,
                    "extract" : extract_r,
                    "asserts" : asserts_r
                }
            )
        return {
            "kind"          : kind,
            "env"           : env_r,
            "items"         : items,
            "template_vars" : ctx,
            "concurrency"   : max(1, int(batch.concurrency or 1)),
            "fail_fast"     : bool(batch.fail_fast)
        }

    @staticmethod
    def validate_request(
        *,
        kind: NexusKind,
        request: NexusRequest,
        env: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """校验单请求的必填字段与基础结构。"""
        rendered = NexusInspectionService.render_request(kind=kind, request=request, env=env)
        errors   = NexusInspectionService._validate_rendered_request(kind, rendered["request"], rendered["env"])

        return {
            "ok"       : not errors,
            "kind"     : kind,
            "errors"   : errors,
            "rendered" : rendered
        }

    @staticmethod
    def validate_batch(
        *,
        kind: NexusKind,
        batch: NexusBatchRequest
    ) -> dict[str, typing.Any]:
        """逐项校验批量请求的必填字段与基础结构。"""
        rendered = NexusInspectionService.render_batch(kind=kind, batch=batch)

        errors: list[dict[str, typing.Any]] = []
        for index, item in enumerate(rendered["items"]):
            item_errors = NexusInspectionService._validate_rendered_request(kind, item["request"], rendered["env"])
            if item_errors:
                errors.append({"index": index, "name": item.get("name"), "errors": item_errors})
        return {
            "ok"       : not errors,
            "kind"     : kind,
            "errors"   : errors,
            "rendered" : rendered
        }

    @staticmethod
    def _validate_rendered_request(
        kind: NexusKind,
        request: dict[str, typing.Any],
        env: dict[str, typing.Any]
    ) -> list[str]:
        """按协议类型校验渲染后的请求内容。"""
        errors: list[str] = []

        def _required_str(label: str, value: typing.Any) -> None:
            if not str(value or "").strip():
                errors.append(f"missing required field: {label}")

        def _required_port(value: typing.Any, label: str = "port") -> None:
            try:
                if int(value) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"invalid required field: {label}")

        if kind in {"http", "sse", "graphql", "ws"}:
            _required_str("url", request.get("url") or env.get("url"))

        if kind == "http":
            pass

        elif kind == "sse":
            pass

        elif kind == "graphql":
            _required_str("query", request.get("query"))

        elif kind == "ws":
            pass

        elif kind in {"tcp", "udp", "smtp", "imap", "ftp"}:
            _required_str("host", request.get("host") or env.get("host"))
            _required_port(request.get("port", env.get("port")))

            if kind == "imap":
                _required_str("username", request.get("username") or env.get("username"))
                _required_str("password", request.get("password") or env.get("password"))

            if kind == "smtp":
                smtp_action = str(request.get("action", env.get("action", "noop"))).strip().lower()
                if smtp_action not in {"noop", "send"}:
                    errors.append(f"unsupported smtp action: {smtp_action}")

                if smtp_action == "send":
                    _required_str("from_addr", request.get("from_addr") or env.get("from_addr"))
                    to_addrs = request.get("to_addrs", env.get("to_addrs"))
                    if not to_addrs:
                        errors.append("missing required field: to_addrs")

            if kind == "ftp" and str(
                request.get("action", env.get("action", "list"))
            ).lower() in {"upload_text", "download_text", "delete", "mkdir"}:
                _required_str("path", request.get("path") or env.get("path"))

            if kind == "ftp" and str(
                request.get("action", env.get("action", "list"))
            ).lower() in {"upload_binary", "download_binary"}:
                _required_str("path", request.get("path") or env.get("path"))

        return errors


if __name__ == '__main__':
    pass
