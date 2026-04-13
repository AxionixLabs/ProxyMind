# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from loguru import logger
from backend.models.model_nexus import (
    NexusKind, NexusRequest, NexusBatchRequest
)
from backend.mcp_hub.hub_nexus.domain.merge import MergeService
from backend.mcp_hub.hub_nexus.domain.template import TemplateService
from backend.utilities.trace import (
    clip_text, summarize_args, summarize_request_target
)


class InspectionService(object):
    """在执行前渲染并校验标准化后的 Nexus 请求。"""

    @staticmethod
    def render_request(
        *,
        kind: NexusKind,
        request: NexusRequest,
        env: typing.Optional[dict[str, typing.Any]] = None
    ) -> dict[str, typing.Any]:
        """渲染单请求中的模板变量与共享默认值。"""
        logger.debug(
            f"inspect render request kind={kind} name={request.name} "
            f"env={summarize_args(env or {})} template_vars={summarize_args(request.template_vars or {})}"
        )
        ctx = dict(request.template_vars or {})

        env_r     = TemplateService.render(dict(env or {}), ctx, path="inspect.env") if env else {}
        request_r = TemplateService.render(dict(request.request or {}), ctx, path="inspect.request")

        final_request = MergeService.materialize(env=env_r, request=request_r)

        extract_r = TemplateService.render(request.extract, ctx, path="inspect.extract") if request.extract else None
        asserts_r = TemplateService.render(request.asserts, ctx, path="inspect.asserts") if request.asserts else None

        logger.debug(
            f"inspect render request done kind={kind} name={request.name} "
            f"target={summarize_request_target(final_request)} request={summarize_args(final_request)}"
        )

        return {
            "kind"          : kind,
            "name"          : request.name,
            "env"           : env_r,
            "request"       : final_request,
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
        logger.debug(
            f"inspect render batch kind={kind} items={len(batch.items)} "
            f"concurrency={max(1, int(batch.concurrency or 1))} fail_fast={bool(batch.fail_fast)} "
            f"env={summarize_args(batch.env or {})} template_vars={summarize_args(batch.template_vars or {})}"
        )
        ctx = dict(batch.template_vars or {})

        env_r = TemplateService.render(dict(batch.env or {}), ctx, path="inspect.batch.env") if batch.env else {}

        items = []

        for item in batch.items:
            request_r     = TemplateService.render(dict(item.request or {}), ctx, path=f"inspect.batch.items[{len(items)}].request")
            final_request = MergeService.materialize(env=env_r, request=request_r)

            extract_r = TemplateService.render(
                item.extract, ctx, path=f"inspect.batch.items[{len(items)}].extract"
            ) if item.extract else None
            asserts_r = TemplateService.render(
                item.asserts, ctx, path=f"inspect.batch.items[{len(items)}].asserts"
            ) if item.asserts else None

            items.append(
                {
                    "name"    : item.name,
                    "request" : final_request,
                    "extract" : extract_r,
                    "asserts" : asserts_r
                }
            )
        logger.debug(
            f"inspect render batch done kind={kind} items={len(items)} "
            f"sample={summarize_args({'items': items[:2]})}"
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
        rendered      = InspectionService.render_request(kind=kind, request=request, env=env)
        error_details = InspectionService._validate_rendered_request(kind, rendered["request"], {})

        errors = [str(item.get("message")) for item in error_details]
        if errors:
            logger.warning(
                f"inspect validate request fail kind={kind} name={request.name} "
                f"target={summarize_request_target(rendered['request'])} "
                f"errors={clip_text(str(error_details), limit=420)}"
            )
        else:
            logger.debug(
                f"inspect validate request ok kind={kind} name={request.name} "
                f"target={summarize_request_target(rendered['request'])}"
            )

        return {
            "ok"            : not errors,
            "kind"          : kind,
            "errors"        : errors,
            "error_details" : error_details,
            "rendered"      : rendered
        }

    @staticmethod
    def validate_batch(
        *,
        kind: NexusKind,
        batch: NexusBatchRequest
    ) -> dict[str, typing.Any]:
        """逐项校验批量请求的必填字段与基础结构。"""
        rendered = InspectionService.render_batch(kind=kind, batch=batch)

        errors: list[dict[str, typing.Any]] = []
        for index, item in enumerate(rendered["items"]):
            item_error_details = InspectionService._validate_rendered_request(kind, item["request"], {})
            if item_error_details:
                errors.append(
                    {
                        "index"         : index,
                        "name"          : item.get("name"),
                        "target"        : summarize_request_target(item["request"]),
                        "errors"        : [str(detail.get("message")) for detail in item_error_details],
                        "error_details" : item_error_details
                    }
                )
        if errors:
            logger.warning(
                f"inspect validate batch fail kind={kind} items={len(rendered['items'])} "
                f"errors={clip_text(str(errors[:3]), limit=520)}"
            )
        else:
            logger.debug(
                f"inspect validate batch ok kind={kind} items={len(rendered['items'])}"
            )

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
    ) -> list[dict[str, typing.Any]]:
        """按协议类型校验渲染后的请求内容。"""
        errors: list[dict[str, typing.Any]] = []

        def _field_source(label: str) -> str:
            if label in request:
                return "request"
            if label in env:
                return "env"
            return "missing"

        def _add_error(
            *,
            field: str,
            message: str,
            value: typing.Any = None,
            source: str = "request",
            code: str = "invalid_field"
        ) -> None:
            errors.append(
                {
                    "field"   : field,
                    "code"    : code,
                    "source"  : source,
                    "value"   : clip_text(value),
                    "message" : message
                }
            )

        def _required_str(label: str, value: typing.Any) -> None:
            if not str(value or "").strip():
                _add_error(
                    field=label,
                    code="missing_required",
                    source=_field_source(label),
                    value=value,
                    message=f"missing required field: {label}"
                )

        def _required_port(value: typing.Any, label: str = "port") -> None:
            try:
                if int(value) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                _add_error(
                    field=label,
                    code="invalid_required",
                    source=_field_source(label),
                    value=value,
                    message=f"invalid required field: {label}"
                )

        if kind in {"http", "sse", "graphql", "ws"}:
            _required_str("url", request.get("url") or env.get("url"))

        if kind == "http":
            pass

        elif kind == "sse":
            pass

        elif kind == "graphql":
            _required_str("query", request.get("query") or env.get("query"))

        elif kind == "ws":
            pass

        elif kind in {"tcp", "udp", "smtp", "imap", "ftp"}:
            _required_str("host", request.get("host") or env.get("host"))
            _required_port(request.get("port", env.get("port")))

            if kind == "imap":
                _required_str("username", request.get("username") or env.get("username"))
                _required_str("password", request.get("password") or env.get("password"))
                imap_action = str(request.get("action", env.get("action", "search"))).strip().lower()
                if imap_action not in {"search", "fetch", "noop"}:
                    _add_error(
                        field="action",
                        code="unsupported_action",
                        value=imap_action,
                        message=f"unsupported imap action: {imap_action}"
                    )

            if kind == "smtp":
                smtp_action = str(request.get("action", env.get("action", "noop"))).strip().lower()
                if smtp_action not in {"noop", "send"}:
                    _add_error(
                        field="action",
                        code="unsupported_action",
                        value=smtp_action,
                        message=f"unsupported smtp action: {smtp_action}"
                    )

                if smtp_action == "send":
                    _required_str("from_addr", request.get("from_addr") or env.get("from_addr"))
                    to_addrs = request.get("to_addrs", env.get("to_addrs"))
                    if not to_addrs:
                        _add_error(
                            field="to_addrs",
                            code="missing_required",
                            source=_field_source("to_addrs"),
                            value=to_addrs,
                            message="missing required field: to_addrs"
                        )

            if kind == "ftp":
                ftp_action = str(request.get("action", env.get("action", "list"))).strip().lower()
                if ftp_action not in {
                    "list", "download_text", "download_binary", "upload_text",
                    "upload_binary", "delete", "mkdir"
                }:
                    _add_error(
                        field="action",
                        code="unsupported_action",
                        value=ftp_action,
                        message=f"unsupported ftp action: {ftp_action}"
                    )
                if ftp_action in {
                    "upload_text", "download_text", "delete", "mkdir",
                    "upload_binary", "download_binary"
                }:
                    _required_str("path", request.get("path") or env.get("path"))
                if ftp_action == "upload_text":
                    _required_str("payload_text", request.get("payload_text") or env.get("payload_text"))
                if ftp_action == "upload_binary":
                    _required_str("payload_base64", request.get("payload_base64") or env.get("payload_base64"))

        return errors


if __name__ == '__main__':
    pass
