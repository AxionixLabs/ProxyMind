# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
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


def render_mind_call_curl(example: dict[str, typing.Any]) -> str | None:
    """把服务端下发的结构化 `mind_call` 示例渲染成多行 curl。"""
    method_raw  = example.get("method")
    url_raw     = example.get("url")
    headers_raw = example.get("headers")
    body_raw    = example.get("body")

    if not isinstance(method_raw, str) or not method_raw.strip():
        return None
    if not isinstance(url_raw, str) or not url_raw.strip():
        return None
    if not isinstance(headers_raw, dict):
        return None
    if body_raw is not None and not isinstance(body_raw, dict):
        return None

    method = method_raw.strip().upper()
    url    = url_raw.strip()
    lines  = [f'curl -X {method} "{url}"']

    for key, value in headers_raw.items():
        if key in (None, "") or value in (None, ""):
            continue
        lines.append(f'  -H "{str(key)}: {str(value)}"')

    if body_raw:
        body_text = json.dumps(body_raw, ensure_ascii=False, indent=2)
        lines.append(f"  -d '{body_text}'")

    return " \\\n".join(lines)


def log_external_access(runtime: AgentSessionRuntime) -> None:
    """打印服务端下发的外部访问令牌和接口调用示例。"""
    if not runtime.access_token:
        logger.debug("[Agent] access token missing")
        return None

    mind_call_example = runtime.mind_call_example or {}
    curl_text = render_mind_call_curl(mind_call_example) if isinstance(mind_call_example, dict) else None
    if isinstance(curl_text, str) and curl_text.strip():
        Design.console.print(f"{curl_text}\n")
        return None

    curl_raw = mind_call_example.get("curl") if isinstance(mind_call_example, dict) else None
    if isinstance(curl_raw, str) and curl_raw.strip():
        Design.console.print(f"{curl_raw}\n")
        return None

    logger.debug("[Agent] mind_call example missing")


if __name__ == '__main__':
    pass
