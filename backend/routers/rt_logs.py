# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import APIRouter
from fastapi.responses import Response
from backend.utilities.storage.logs import read_log_lines
from .page import render_page

logs_router = APIRouter(tags=["Logs"])


@logs_router.get(path="/logs", include_in_schema=False)
async def api_logs_page() -> Response:
    """
    返回日志查看页面 HTML。

    请求参数:
        无。

    返回:
        Response: text/html 响应，内容来自 logs.html。
    """
    return render_page("logs.html")


@logs_router.get(path="/api/logs", include_in_schema=False)
async def api_logs() -> dict:
    """
    读取日志尾部内容。

    请求参数:
        无。

    返回:
        {
          "ok": true,
          "data": {
            "exists": true,
            "path": "D:/path/helix.log",
            "lines": ["..."],
            "line_count": 120,
            "truncated": false,
            "size": 4096
          }
        }
    """
    return {
        "ok"   : True,
        "data" : read_log_lines(max_lines=500)
    }


if __name__ == '__main__':
    pass
