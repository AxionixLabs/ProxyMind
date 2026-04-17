# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import APIRouter
from fastapi.responses import Response
from backend.utilities import const
from .page import render_page

basic_router = APIRouter(tags=["Basic"])


@basic_router.get(path="/", include_in_schema=False)
async def api_root() -> Response:
    return render_page("index.html")


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
