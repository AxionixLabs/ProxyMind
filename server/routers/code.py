# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from fastapi import APIRouter
from fastapi.responses import Response
from ..page import render_page

code_router = APIRouter(tags=["Code"])


@code_router.get(path="/code", include_in_schema=False)
async def api_code_page() -> Response:
    """返回代码页面。"""
    return render_page("code.html")


if __name__ == "__main__":
    pass
