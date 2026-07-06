# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from loguru import logger
from mind_core.design import Design
from .models import (
    AgentLiveStatus, AgentSessionRuntime
)
from server import config_service_base_url

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


async def start_connect_animation(mind: "Mind", live_status: AgentLiveStatus) -> None:
    """启动建连等待动画。"""
    await mind.anim_manager.start(
        lambda stop_event: mind.design.agent_connect_live(stop_event, live_status.snapshot)
    )


async def start_status_animation(mind: "Mind", live_status: AgentLiveStatus) -> None:
    """启动订阅读取状态动画。"""
    await mind.anim_manager.start(
        lambda stop_event: mind.design.agent_wait_live(stop_event, live_status.snapshot)
    )


async def publish_external_access(runtime: AgentSessionRuntime) -> None:
    """把当前会话示例推送到本地页面。"""
    example = runtime.mind_call_example
    if not isinstance(example, dict):
        logger.debug("[Agent] mind_call example missing")
        return None

    if not runtime.credential:
        logger.debug("[Agent] credential missing")

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
        logger.debug(
            f"[Agent] agent page sync failed: {type(exc).__name__}: {exc}"
        )
        return None


def show_external_access_link() -> None:
    """输出本地 agent 示例页面链接。"""
    Design.console.print(f"🌐 Agent: {config_service_base_url()}/agent\n")


if __name__ == '__main__':
    pass
