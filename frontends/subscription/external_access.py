# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Callable

import httpx

from observability import (
    observe,
    observe_exception,
)
from .models import AgentSessionRuntime


async def publish_external_access(
    runtime: AgentSessionRuntime,
    *,
    configuration_service_url: Callable[[], str] | None,
) -> None:
    """把当前订阅会话的调用示例同步到本地配置页面。"""
    example = runtime.mind_call_example
    if not isinstance(example, dict):
        observe("agent.page_sync.skipped", reason="mind_call_example_missing")
        return None

    if not runtime.credential:
        observe("agent.page_sync.skipped", reason="credential_missing")

    try:
        if configuration_service_url is None:
            observe(
                "agent.page_sync.skipped",
                reason="configuration_service_unavailable",
            )
            return None
        base_url = configuration_service_url()
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
