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
    """把服务端下发的结构化调用示例渲染成多行 curl。"""
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


def render_mind_call_example(example: dict[str, typing.Any]) -> str | None:
    """把单条调用示例渲染成可直接阅读的文本块。"""
    title_raw = example.get("title")
    id_raw    = example.get("id")

    title = str(title_raw).strip() if isinstance(title_raw, str) else ""
    example_id = str(id_raw).strip() if isinstance(id_raw, str) else ""

    header_parts: list[str] = []
    if title:
        header_parts.append(title)
    if example_id:
        header_parts.append(f"id={example_id}")

    curl_text = render_mind_call_curl(example)

    if not curl_text:
        return None

    if header_parts:
        return f"{' | '.join(header_parts)}\n{curl_text}"
    return curl_text


def log_external_access(runtime: AgentSessionRuntime) -> None:
    """打印服务端下发的外部访问令牌和接口调用示例。"""
    if not runtime.access_token:
        logger.debug("[Agent] access token missing")
        return None

    examples = runtime.mind_call_examples or []
    rendered_blocks: list[str] = []
    for example in examples:
        if isinstance(example, dict):
            rendered = render_mind_call_example(example)
            if rendered:
                rendered_blocks.append(rendered)

    if rendered_blocks:
        Design.console.print("\n\n".join(rendered_blocks) + "\n")
        return None

    logger.debug("[Agent] mind_call examples missing")


if __name__ == '__main__':
    pass
