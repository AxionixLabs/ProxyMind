# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import APIRouter
from fastapi.responses import Response
from backend.utilities import const
from .page import render_page

basic_router = APIRouter(tags=["Basic"])


@basic_router.get(path="/", include_in_schema=False)
async def api_root() -> Response:
    """返回运行时工作台页面。"""
    return render_page("index.html")


@basic_router.get(path="/ready", include_in_schema=False)
async def api_ready() -> dict:
    """
    返回服务就绪状态。

    请求参数:
        无。

    返回:
        {
          "ready": true
        }
    """
    return {
        "ready" : True
    }


@basic_router.get(path="/healthz", include_in_schema=False)
async def api_healthz() -> dict:
    """
    返回健康检查信息。

    请求参数:
        无。

    返回:
        {
          "ok": true,
          "service": "helix mcp",
          "transport": "streamable-http"
        }
    """
    return {
        "ok"        : True,
        "service"   : f"{const.APP_NAME} mcp",
        "transport" : "streamable-http"
    }


@basic_router.get(path="/version", include_in_schema=False)
async def api_version() -> dict:
    """
    返回服务版本信息。

    请求参数:
        无。

    返回:
        {
          "ok": true,
          "service": "helix mcp",
          "version": "1.0.0"
        }
    """
    return {
        "ok"      : True,
        "service" : f"{const.APP_NAME} mcp",
        "version" : const.APP_VERSION
    }


if __name__ == '__main__':
    pass
