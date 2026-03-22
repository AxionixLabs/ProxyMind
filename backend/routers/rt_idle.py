#  ____            __   ____             _
# |  _ \ _ __ ___ / _| |  _ \ ___  _   _| |_ ___ _ __
# | |_) | '__/ _ \ |_  | |_) / _ \| | | | __/ _ \ '__|
# |  __/| | |  __/  _| |  _ < (_) | |_| | ||  __/ |
# |_|   |_|  \___|_|   |_| \_\___/ \__,_|\__\___|_|
#
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


if __name__ == '__main__':
    pass
