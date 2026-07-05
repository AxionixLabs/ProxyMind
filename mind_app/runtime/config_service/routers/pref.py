# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from ..page import render_page
from ..storage import (
    load_pref, save_pref
)

pref_router = APIRouter(tags=["Pref"])


@pref_router.get(path="/pref", include_in_schema=False)
async def api_pref_page() -> Response:
    """返回偏好设置页面。"""
    return render_page("pref.html")


@pref_router.get(path="/api/pref", include_in_schema=False)
async def api_pref_load() -> dict[str, typing.Any]:
    """读取偏好配置。"""
    return {
        "ok"   : True,
        "data" : load_pref()
    }


@pref_router.put(path="/api/pref", include_in_schema=False)
async def api_pref_save(request: Request) -> dict[str, typing.Any]:
    """保存偏好配置。"""
    payload = await request.json()

    return {
        "ok"   : True,
        "data" : save_pref(payload)
    }


if __name__ == "__main__":
    pass
