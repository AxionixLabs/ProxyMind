# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from backend.utilities import const
from .page import render_page

idle_router = APIRouter(tags=["Idle"])


@idle_router.get(path="/idle", include_in_schema=False)
async def api_idle_page() -> Response:
    """
    返回 idle 状态页面 HTML，并禁用浏览器缓存。

    请求参数:
        无。

    返回:
        Response: text/html 响应，内容来自 idle.html，附带 no-store 缓存头。
    """
    page = render_page("idle.html")
    return Response(
        page.body,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control" : "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma"        : "no-cache",
            "Expires"       : "0"
        }
    )


@idle_router.get(path="/api/idle", include_in_schema=False)
async def api_idle(request: Request) -> Response:
    """
    返回当前 idle 运行态快照。

    请求参数:
        无。

    返回:
        {
          "ttl_sec": 1800.0,
          "active_runtime_jobs": 0,
          "active_sessions": 0,
          "active_total": 0,
          "idle_sec": 12.3,
          "jobs": [],
          "sessions": {}
        }
    """
    data = await request.app.state.idle.snapshot()
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8"
    )


@idle_router.get(path="/api/keepalive", include_in_schema=False)
async def api_keepalive(request: Request) -> dict:
    """
    刷新 idle 活动时间并返回 keepalive 状态。

    请求参数:
        无。

    返回:
        {
          "ok": true,
          "service": "helix keepalive",
          "ttl_sec": 1800.0,
          "idle_sec": 0.0,
          "keepalive_sec": 300.0,
          "active_total": 0
        }
    """
    await request.app.state.idle.touch()

    data = await request.app.state.idle.snapshot()

    return {
        "ok"            : True,
        "service"       : f"{const.APP_NAME} keepalive",
        "ttl_sec"       : data.get("ttl_sec"),
        "idle_sec"      : data.get("idle_sec"),
        "keepalive_sec" : const.KEEPALIVE_SEC,
        "active_total"  : data.get("active_total")
    }


if __name__ == '__main__':
    pass
