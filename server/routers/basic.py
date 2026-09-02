# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from fastapi import APIRouter
from fastapi.responses import Response

from metadata import const
from ..page import render_page

basic_router = APIRouter(tags=["MindConfig"])


@basic_router.get(path="/", include_in_schema=False)
async def api_root() -> Response:
    """返回配置服务首页。"""
    return render_page("index.html")


@basic_router.get(path="/ready", include_in_schema=False)
async def api_ready() -> dict[str, bool]:
    """返回配置服务就绪状态。"""
    return {"ready": True}


@basic_router.get(path="/healthz", include_in_schema=False)
async def api_healthz() -> dict[str, str | bool]:
    """返回配置服务健康状态。"""
    return {
        "ok": True,
        "service": "configuration",
        "transport": "http"
    }


@basic_router.get(path="/version", include_in_schema=False)
async def api_version() -> dict[str, str | bool]:
    """返回配置服务版本信息。"""
    return {
        "ok": True,
        "service": "configuration",
        "version": const.APP_VERSION
    }


if __name__ == "__main__":
    pass
