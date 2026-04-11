# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
from loguru import logger
from mind_core.design import Design
from ...runtime.keepalive import run_keepalive
from .models import (
    AgentLiveStatus, AgentSessionRuntime
)
from mind_nova import const

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


def ensure_agent_keepalive(
    runtime: AgentSessionRuntime,
    stop_event: asyncio.Event
) -> None:
    """确保 agent 模式在后台维持 keepalive。"""
    tasks = runtime.pending_tasks if runtime.pending_tasks is not None else set()
    runtime.pending_tasks = tasks

    for task in list(tasks):
        if task.get_name() == "agent keepalive" and not task.done():
            return None

    async def runner() -> None:
        try:
            await run_keepalive(stop_event)
        except asyncio.CancelledError:
            logger.debug("[Agent] keepalive cancelled")
            raise

    task = asyncio.create_task(runner(), name="agent keepalive")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def publish_external_access(runtime: AgentSessionRuntime) -> None:
    """把当前会话示例推送到本地页面。"""
    example = runtime.mind_call_example
    if not isinstance(example, dict):
        logger.debug("[Agent] mind_call example missing")
        return None

    if not runtime.credential:
        logger.debug("[Agent] credential missing")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.5)) as http:
            response = await http.put(
                f"{const.BASE_URL}/api/agent",
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
    Design.console.print(f"🌐 Agent: {const.BASE_URL}/agent\n")


if __name__ == '__main__':
    pass
