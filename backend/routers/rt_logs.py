# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import APIRouter
from fastapi.responses import Response
from backend.utilities.storage.logs import read_log_lines
from .page import render_page

logs_router = APIRouter(tags=["Logs"])


@logs_router.get(path="/logs", include_in_schema=False)
async def api_logs_page() -> Response:
    return render_page("logs.html")


@logs_router.get(path="/api/logs", include_in_schema=False)
async def api_logs() -> dict:
    return {
        "ok"   : True,
        "data" : read_log_lines(max_lines=500)
    }


if __name__ == '__main__':
    pass
