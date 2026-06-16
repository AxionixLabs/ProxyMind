# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from .page import render_page

agent_router = APIRouter(tags=["Agent"])


@agent_router.get(path="/agent", include_in_schema=False)
async def api_agent_page() -> Response:
    """
    返回 agent 示例页面 HTML。

    请求参数:
        无。

    返回:
        Response: text/html 响应，内容来自 agent.html。
    """
    return render_page("agent.html")


@agent_router.get(path="/api/agent", include_in_schema=False)
async def api_agent_load(request: Request) -> dict[str, typing.Any]:
    """
    读取本地 agent 示例数据。

    请求参数:
        无。

    返回:
        {
          "ok": true,
          "data": {
            "session_id": "",
            "credential": "",
            "credential_tail": "",
            "has_credential": false,
            "mind_call": null,
            "updated_at_ms": 0
          }
        }
    """

    def _empty_agent_data() -> dict[str, typing.Any]:
        return {
            "session_id"      : "",
            "credential"      : "",
            "credential_tail" : "",
            "has_credential"  : False,
            "mind_call"       : None,
            "updated_at_ms"   : 0
        }

    data = getattr(request.app.state, "agent_example", None) or _empty_agent_data()
    return {
        "ok"   : True,
        "data" : data
    }


@agent_router.put(path="/api/agent", include_in_schema=False)
async def api_agent_save(request: Request) -> dict[str, typing.Any]:
    """
    保存本地 agent 示例数据。

    请求体 payload:
        {
          "session_id": "session-xxx",
          "credential": "secret-token",
          "mind_call": {
            "name": "tool_name",
            "arguments": {}
          }
        }

    返回:
        {
          "ok": true,
          "data": {
            "session_id": "session-xxx",
            "credential": "secret-token",
            "credential_tail": "et-token",
            "has_credential": true,
            "mind_call": {
              "name": "tool_name",
              "arguments": {}
            },
            "updated_at_ms": 1710000000000
          }
        }
    """

    def _normalize_agent_payload(body: dict[str, typing.Any]) -> dict[str, typing.Any]:
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

    payload = await request.json()

    normalized = _normalize_agent_payload(payload)

    request.app.state.agent_example = normalized

    return {
        "ok"   : True,
        "data" : normalized
    }


if __name__ == '__main__':
    pass
