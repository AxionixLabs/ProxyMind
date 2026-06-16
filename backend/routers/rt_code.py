# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi import APIRouter
from fastapi.responses import Response
from .page import render_page

code_router = APIRouter(tags=["Code"])


@code_router.get(path="/code", include_in_schema=False)
async def api_code_page() -> Response:
    """
    返回代码页面 HTML。

    请求参数:
        无。

    返回:
        Response: text/html 响应，内容来自 code.html。
    """
    return render_page("code.html")


if __name__ == '__main__':
    pass
