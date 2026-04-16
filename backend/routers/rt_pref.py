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
    return render_page("pref.html")


@pref_router.get(path="/api/pref", include_in_schema=False)
async def api_pref_load() -> dict:
    return {
        "ok"   : True,
        "data" : load_pref()
    }


@pref_router.put(path="/api/pref", include_in_schema=False)
async def api_pref_save(request: Request) -> dict:
    payload = await request.json()
    return {
        "ok"   : True,
        "data" : save_pref(payload)
    }


if __name__ == '__main__':
    pass
