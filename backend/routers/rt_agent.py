# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
from pathlib import Path
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from backend.utilities import const

agent_router = APIRouter(tags=["Agent"])


@agent_router.get(path="/agent", include_in_schema=False)
async def api_agent_page() -> Response:
    html = Path(__file__).resolve().parent.parent / "web" / "agent.html"
    html = html.read_text(encoding=const.CHARSET, errors="replace")
    html = html.replace("__APP_VERSION__", const.APP_VERSION)
    return Response(html, media_type="text/html; charset=utf-8")


@agent_router.get(path="/api/agent", include_in_schema=False)
async def api_agent_load(request: Request) -> dict[str, typing.Any]:

    def _empty_agent_data() -> dict[str, typing.Any]:
        """返回 agent 页面默认展示的数据结构。"""
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

    def _normalize_agent_payload(body: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """收敛本地 agent 页面使用的最新示例数据。"""
        body = body if isinstance(body, dict) else {}

        session_id_raw = body.get("session_id")
        session_id     = session_id_raw.strip() if isinstance(session_id_raw, str) else ""

        credential_raw = body.get("credential")
        credential     = credential_raw.strip() if isinstance(credential_raw, str) else ""

        mind_call_raw = body.get("mind_call")
        mind_call     = mind_call_raw if isinstance(mind_call_raw, dict) else None

        return {
            "session_id": session_id,
            "credential": credential,
            "credential_tail": credential[-8:] if credential else "",
            "has_credential": bool(credential),
            "mind_call": mind_call,
            "updated_at_ms": int(time.time() * 1000)
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
