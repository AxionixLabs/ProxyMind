# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
from observability import (
    observe,
    observe_exception,
)
from server import config_service_base_url
from .models import AgentSessionRuntime


async def publish_external_access(runtime: AgentSessionRuntime) -> None:
    """把当前订阅会话的调用示例同步到本地配置页面。"""
    example = runtime.mind_call_example
    if not isinstance(example, dict):
        observe("agent.page_sync.skipped", reason="mind_call_example_missing")
        return None

    if not runtime.credential:
        observe("agent.page_sync.skipped", reason="credential_missing")

    try:
        base_url = config_service_base_url()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(3.0, connect=1.5),
            trust_env=False,
        ) as http:
            response = await http.put(
                f"{base_url}/api/agent",
                headers={"Content-Type": "application/json"},
                json={
                    "session_id": runtime.session_id,
                    "credential": runtime.credential,
                    "mind_call": example,
                },
            )
        response.raise_for_status()
    except (OSError, httpx.HTTPError, ValueError) as exc:
        observe_exception("agent.page_sync.failed", exc, level="WARNING")
        return None

    observe(
        "agent.page_sync.complete",
        session_id=runtime.session_id,
        has_credential=bool(runtime.credential),
    )


if __name__ == '__main__':
    pass
