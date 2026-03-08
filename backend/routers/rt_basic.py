#  ____            _        ____             _
# | __ )  __ _ ___(_) ___  |  _ \ ___  _   _| |_ ___ _ __
# |  _ \ / _` / __| |/ __| | |_) / _ \| | | | __/ _ \ '__|
# | |_) | (_| \__ \ | (__  |  _ < (_) | |_| | ||  __/ |
# |____/ \__,_|___/_|\___| |_| \_\___/ \__,_|\__\___|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import json
from pathlib import Path
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from backend.utilities import const

basic_router = APIRouter(tags=["Basic"])


@basic_router.get(path="/", include_in_schema=False)
async def api_root() -> Response:
    html = Path(__file__).resolve().parent.parent / "web" / "index.html"
    html = html.read_text(encoding=const.CHARSET, errors="replace")
    html = html.replace("__APP_VERSION__", const.APP_VERSION)
    return Response(html, media_type="text/html; charset=utf-8")


@basic_router.get(path="/idle", include_in_schema=False)
async def api_idle(request: Request) -> Response:
    data = await request.app.state.idle.snapshot()
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8"
    )


@basic_router.get(path="/ready", include_in_schema=False)
async def api_ready() -> dict:
    return {
        "ready" : True
    }


@basic_router.get(path="/healthz", include_in_schema=False)
async def api_healthz() -> dict:
    return {
        "ok"        : True,
        "service"   : f"{const.APP_NAME} mcp",
        "transport" : "streamable-http"
    }


@basic_router.get(path="/version", include_in_schema=False)
async def api_version() -> dict:
    return {
        "ok"      : True,
        "service" : f"{const.APP_NAME} mcp",
        "version" : const.APP_VERSION
    }


if __name__ == '__main__':
    pass
