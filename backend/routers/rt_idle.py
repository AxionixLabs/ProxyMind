# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
from pathlib import Path
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from backend.utilities import const

idle_router = APIRouter(tags=["Idle"])


@idle_router.get(path="/idle", include_in_schema=False)
async def api_idle_page() -> Response:
    html = Path(__file__).resolve().parent.parent / "web" / "idle.html"
    html = html.read_text(encoding=const.CHARSET, errors="replace")
    html = html.replace("__APP_VERSION__", const.APP_VERSION)
    return Response(html, media_type="text/html; charset=utf-8")


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

    续命动作由全局 HTTP middleware 统一执行，这里只负责回传状态与周期，
    不再额外调用 `idle.touch()`，避免形成重复语义。
    """
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
