# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from mind_nova.services import service_endpoints
from mind_nova.requests.reliable import post_json_reliably
from mind_nova.service_auth import build_service_headers


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
        "result_payload": dict(result_payload or {}),
        "error": str(error or ""),
        "metadata": dict(metadata or {}),
    }
    response = await post_json_reliably(
        service_endpoints.endpoint("/effect/reconcile"),
        headers=build_service_headers(),
        payload=payload,
        timeout=timeout,
        client_factory=httpx.AsyncClient,
    )
    response.raise_for_status()
    body = response.json()
    if (
        not isinstance(body, dict)
        or body.get("ok") is not True
        or body.get("status") != "reconciled"
        or not isinstance(body.get("effect"), dict)
    ):
        raise RuntimeError("effect reconciliation response is invalid")
    return body


if __name__ == "__main__":
    pass
