# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import (
    APIRouter, Request
)
from fastapi.responses import Response
from backend.utilities.storage.prefs import (
    load_pref, save_pref
)
from .page import render_page

pref_router = APIRouter(tags=["Pref"])


@pref_router.get(path="/pref", include_in_schema=False)
async def api_pref_page() -> Response:
    """
    返回偏好设置页面 HTML。

    请求参数:
        无。

    返回:
        Response: text/html 响应，内容来自 pref.html。
    """
    return render_page("pref.html")


@pref_router.get(path="/api/pref", include_in_schema=False)
async def api_pref_load() -> dict:
    """
    读取偏好配置。

    请求参数:
        无。

    返回:
        {
          "ok": true,
          "data": {
            "schema_version": 2,
            "profile_key": "default",
            "primary": {
              "api": "OpenAI",
              "base_url": "https://api.openai.com/v1",
              "route": "responses",
              "apikey": "sk-xxx",
              "model": "gpt-4.1",
              "type": "Text",
              "notes": ""
            },
            "secondary": null
          }
        }
    """
    return {
        "ok"   : True,
        "data" : load_pref()
    }


@pref_router.put(path="/api/pref", include_in_schema=False)
async def api_pref_save(request: Request) -> dict:
    """
    保存偏好配置。

    请求体 payload:
        {
          "primary": {
            "api": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "route": "responses",
            "apikey": "sk-xxx",
            "model": "gpt-4.1",
            "type": "Text",
            "notes": ""
          },
          "secondary": {
            "api": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "route": "responses",
            "apikey": "sk-yyy",
            "model": "gpt-4.1-mini",
            "type": "Text",
            "notes": ""
          }
        }

    返回:
        {
          "ok": true,
          "data": {
            "schema_version": 2,
            "profile_key": "default",
            "primary": {
              "api": "OpenAI",
              "base_url": "https://api.openai.com/v1",
              "route": "responses",
              "apikey": "sk-xxx",
              "model": "gpt-4.1",
              "type": "Text",
              "notes": ""
            },
            "secondary": null
          }
        }
    """
    payload = await request.json()

    return {
        "ok"   : True,
        "data" : save_pref(payload)
    }


if __name__ == '__main__':
    pass
