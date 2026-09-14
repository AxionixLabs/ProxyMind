# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

import httpx

from protocol.client.tools import (
    normalize_tool_result_payload,
    parse_tool_result_error,
)
from protocol.schema.tool_output import (
    project_tool_output_text,
    validate_persistable_json,
)
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from protocol.transport.reliable import post_json_reliably


async def post_effect_reconciliation(
    *,
    effect_id: str,
    request_id: str,
    resolution: typing.Literal["committed", "failed", "retry"],
    result_payload: dict[str, typing.Any] | None = None,
    error: str = "",
    metadata: dict[str, typing.Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, typing.Any]:
    """向控制面提交持久效果的确定核对结论。"""
    payload = {
        "effect_id": str(effect_id or "").strip(),
        "request_id": str(request_id or "").strip(),
        "resolution": resolution,
        "result_payload": normalize_tool_result_payload(result_payload) if result_payload else {},
        "error": project_tool_output_text(str(error or "")),
        "metadata": dict(metadata or {}),
    }
    validate_persistable_json(payload)
    response = await post_json_reliably(
        service_endpoints.endpoint("/effect/reconcile"),
        headers=build_service_headers(),
        payload=payload,
        timeout=timeout,
        client_factory=httpx.AsyncClient,
        retry_delays=(0.0,),
    )
    if response.is_error:
        raise parse_tool_result_error(response)
    body = response.json()
    if (
        not isinstance(body, dict)
        or body.get("ok") is not True
        or body.get("status") != "reconciled"
        or not isinstance(body.get("effect"), dict)
    ):
        raise RuntimeError("effect reconciliation response is invalid")
    return body


if __name__ == '__main__':
    pass
