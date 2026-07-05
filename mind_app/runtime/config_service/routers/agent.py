# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from ..page import render_page

agent_router = APIRouter(tags=["Agent"])


def empty_agent_data() -> dict[str, typing.Any]:
    """返回空的 agent 示例数据。"""
    return {
        "session_id"      : "",
        "credential"      : "",
        "credential_tail" : "",
        "has_credential"  : False,
        "mind_call"       : None,
        "updated_at_ms"   : 0
    }


def normalize_agent_payload(body: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """规范化 agent 示例写入数据。"""
    body = body if isinstance(body, dict) else {}

    session_id_raw = body.get("session_id")
    session_id     = session_id_raw.strip() if isinstance(session_id_raw, str) else ""

    credential_raw = body.get("credential")
    credential     = credential_raw.strip() if isinstance(credential_raw, str) else ""

    mind_call_raw = body.get("mind_call")
    mind_call     = mind_call_raw if isinstance(mind_call_raw, dict) else None

    return {
        "session_id"      : session_id,
        "credential"      : credential,
        "credential_tail" : credential[-8:] if credential else "",
        "has_credential"  : bool(credential),
        "mind_call"       : mind_call,
        "updated_at_ms"   : int(time.time() * 1000)
    }


@agent_router.get(path="/agent", include_in_schema=False)
async def api_agent_page() -> Response:
    """返回 agent 示例页面。"""
    return render_page("agent.html")


@agent_router.get(path="/api/agent", include_in_schema=False)
async def api_agent_load(request: Request) -> dict[str, typing.Any]:
    """读取 agent 示例数据。"""
    data = getattr(request.app.state, "agent_example", None) or empty_agent_data()

    return {
        "ok"   : True,
        "data" : data
    }


@agent_router.put(path="/api/agent", include_in_schema=False)
async def api_agent_save(request: Request) -> dict[str, typing.Any]:
    """保存 agent 示例数据。"""
    payload    = await request.json()
    normalized = normalize_agent_payload(payload)

    request.app.state.agent_example = normalized

    return {
        "ok"   : True,
        "data" : normalized
    }


if __name__ == "__main__":
    pass
