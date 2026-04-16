# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from fastapi.responses import Response
from backend.utilities.paths import resource_path
from backend.utilities import const


def render_page(name: str) -> Response:
    """读取并返回带版本占位替换的静态页面。"""
    html = resource_path("web", name)
    content = html.read_text(encoding=const.CHARSET, errors="replace")
    content = content.replace("__APP_VERSION__", const.APP_VERSION)
    return Response(content, media_type="text/html; charset=utf-8")


if __name__ == '__main__':
    pass
