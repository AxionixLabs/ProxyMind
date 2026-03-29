# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import Response
from backend.utilities import const
from backend.utilities.storage.logs import read_log_lines

logs_router = APIRouter(tags=["Logs"])


@logs_router.get(path="/logs", include_in_schema=False)
async def api_logs_page() -> Response:
    html = Path(__file__).resolve().parent.parent / "web" / "logs.html"
    html = html.read_text(encoding=const.CHARSET, errors="replace")
    html = html.replace("__APP_VERSION__", const.APP_VERSION)
    return Response(html, media_type="text/html; charset=utf-8")


@logs_router.get(path="/api/logs", include_in_schema=False)
async def api_logs() -> dict:
    return {
        "ok"   : True,
        "data" : read_log_lines(max_lines=500)
    }


if __name__ == '__main__':
    pass
