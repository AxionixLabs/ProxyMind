# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Callable
import typing

import httpx

from observability import (
    observe,
    observe_exception,
)
from .models import AgentSessionRuntime

ConnectionState = typing.Literal["idle", "connecting", "online", "reconnecting", "stopped", "error"]


async def publish_external_access(
    runtime: AgentSessionRuntime | None,
    *,
    configuration_service_url: Callable[[], str] | None,
    state: ConnectionState = "connecting",
) -> None:
    """把当前订阅会话的连接资料同步到本地配置页面。"""
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
                    "session_id": runtime.session_id if runtime else "",
                    "credential": runtime.credential if runtime else "",
                    "web_url": runtime.web_url if runtime else "",
                    "state": state,
                },
            )
        response.raise_for_status()
    except (OSError, httpx.HTTPError, ValueError) as exc:
        observe_exception("agent.page_sync.failed", exc, level="WARNING")
        return None

    observe(
        "agent.page_sync.complete",
        level="DEBUG",
        session_id=runtime.session_id if runtime else "",
        has_credential=bool(runtime and runtime.credential),
    )


if __name__ == '__main__':
    pass
