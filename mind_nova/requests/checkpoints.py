# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.channel import Channel
from mind_nova.services import service_endpoints


async def post_checkpoint_artifact(
    *,
    checkpoint_id: str,
    request_id: str,
    artifact: dict[str, typing.Any],
    timeout: float = 30.0
) -> dict[str, typing.Any]:
    """在本地副作用执行前关联 workspace 恢复 artifact。"""
    payload = {
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "request_id": str(request_id or "").strip(),
        "artifact": dict(artifact),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            service_endpoints.endpoint("/checkpoint/artifact"),
            headers=Channel.make_headers(),
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    if not isinstance(body, dict) or body.get("ok") is not True:
        raise RuntimeError("checkpoint artifact response is invalid")

    checkpoint = body.get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise RuntimeError("checkpoint artifact response is missing checkpoint")

    attached = checkpoint.get("artifact")
    if not isinstance(attached, dict) or attached != artifact:
        raise RuntimeError("checkpoint artifact response does not match request")

    return checkpoint


if __name__ == "__main__":
    pass
