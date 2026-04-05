# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import Response
from backend.utilities import const

code_router = APIRouter(tags=["Code"])


@code_router.get(path="/code", include_in_schema=False)
async def api_code_page() -> Response:
    html = Path(__file__).resolve().parent.parent / "web" / "code.html"
    html = html.read_text(encoding=const.CHARSET, errors="replace")
    html = html.replace("__APP_VERSION__", const.APP_VERSION)
    return Response(html, media_type="text/html; charset=utf-8")


if __name__ == '__main__':
    pass
