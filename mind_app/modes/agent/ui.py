# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from loguru import logger
from mind_core.design import Design
from .models import (
    AgentLiveStatus, AgentSessionRuntime
)

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


def log_external_access(runtime: AgentSessionRuntime) -> None:
    """打印服务端下发的外部访问令牌和接口调用示例。"""
    if not runtime.access_token:
        logger.debug("[Agent] access token missing")
        return None

    mind_call_example = runtime.mind_call_example or {}
    curl_raw = mind_call_example.get("curl") if isinstance(mind_call_example, dict) else None
    if isinstance(curl_raw, str) and curl_raw.strip():
        Design.console.print(f"{curl_raw}\n")
        return None

    logger.debug("[Agent] mind_call example missing")


if __name__ == '__main__':
    pass
