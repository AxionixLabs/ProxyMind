# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.observability import (
    observe,
    observe_exception
)
from mind_app.frontend import ApplicationView
from .models import (
    AgentLiveStatus,
    AgentSessionRuntime
)
from server import config_service_base_url

if typing.TYPE_CHECKING:
    from ..controller import Mind


async def start_connect_animation(mind: "Mind", live_status: AgentLiveStatus) -> None:
    """启动建连等待动画。"""
    if not mind.animate:
        return None
    if mind.frontend.runtime.active:
        await mind.frontend.runtime.begin_external_mcp_status(
            lambda: _agent_status_snapshot(live_status)
        )
        return None
    design = mind.require_design()
    await mind.anim_manager.start(
        lambda stop_event: design.agent_connect_live(stop_event, live_status.snapshot)
    )


async def start_status_animation(mind: "Mind", live_status: AgentLiveStatus) -> None:
    """启动订阅读取状态动画。"""
    if not mind.animate:
        return None
    if mind.frontend.runtime.active:
        await mind.frontend.runtime.begin_external_mcp_status(
            lambda: _agent_status_snapshot(live_status)
        )
        return None
    design = mind.require_design()
    await mind.anim_manager.start(
        lambda stop_event: design.agent_wait_live(stop_event, live_status.snapshot)
    )


def _agent_status_snapshot(live_status: AgentLiveStatus) -> dict[str, typing.Any]:
    """把 Agent 状态转换为通用 TUI 活动快照。"""
    value = live_status.snapshot()
    if isinstance(value, tuple):
        title = str(value[0] or "") if value else ""
        detail = str(value[1] or "") if len(value) > 1 else ""
        return {"summary": title, "detail": detail}
    return {"summary": str(value or "Agent")}


async def publish_external_access(runtime: AgentSessionRuntime) -> None:
    """把当前会话示例推送到本地页面。"""
    example = runtime.mind_call_example
    if not isinstance(example, dict):
        observe("agent.page_sync.skipped", reason="mind_call_example_missing")
        return None

    if not runtime.credential:
        observe("agent.page_sync.skipped", reason="credential_missing")

    try:
        base_url = config_service_base_url()
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.5)) as http:
            response = await http.put(
                f"{base_url}/api/agent",
                headers={"Content-Type": "application/json"},
                json={
                    "session_id" : runtime.session_id,
                    "credential" : runtime.credential,
                    "mind_call"  : example
                }
            )
        response.raise_for_status()
    except (OSError, httpx.HTTPError, ValueError) as exc:
        observe_exception("agent.page_sync.failed", exc, level="WARNING")
        return None


def show_external_access_link(mind: "Mind") -> None:
    """输出本地 agent 示例页面链接。"""
    mind.frontend.application.emit(ApplicationView(
        type="agent.external_access",
        renderable=f"🌐 Agent: {config_service_base_url()}/agent",
        end="\n\n",
    ))


if __name__ == '__main__':
    pass
