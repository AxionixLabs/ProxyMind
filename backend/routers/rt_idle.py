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
    data = await request.app.state.idle.snapshot()
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8"
    )


@idle_router.get(path="/api/keepalive", include_in_schema=False)
async def api_keepalive(request: Request) -> dict:
    """
    返回 keepalive 状态。

    该接口显式刷新 idle 最近活动时间，并返回当前状态与建议周期。
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
