# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import uuid
import typing
from loguru import logger
from mind_core.design import Design
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


def build_external_api_example(base_url: str, access_token: str) -> list[str]:
    """构造外部调用示例，便于直接调试服务端下发的访问令牌。"""
    chat_request_id = str(uuid.uuid4())
    mind_chat_payload = {
        "mode"        : "fast",
        "profile"     : "",
        "message"     : "",
        "timeout_sec" : 300
    }
    mind_chat_body = json.dumps(mind_chat_payload, ensure_ascii=False, indent=2)

    return [
        (
            f"curl -X POST {base_url.rstrip('/')}/{const.APP_NAME} \\\n"
            f"  -H 'Authorization: Bearer {access_token}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -H 'Idempotency-Key: {chat_request_id}' \\\n"
            f"  -d '{mind_chat_body}'"
        )
    ]


def log_external_access(runtime: AgentSessionRuntime, base_url: str) -> None:
    """打印服务端下发的外部访问令牌和接口调用示例。"""
    if not runtime.access_token:
        logger.debug("[Agent] access token missing")
        return None

    example = "\n\n".join(build_external_api_example(base_url, runtime.access_token))
    Design.console.print(f"{example}\n")


if __name__ == '__main__':
    pass
