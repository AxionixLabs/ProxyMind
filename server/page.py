# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path

from fastapi.responses import Response

from metadata import const


def web_dir() -> Path:
    """返回配置服务页面目录。"""
    return Path(__file__).resolve().parent.parent / "web"


def render_page(name: str) -> Response:
    """读取并返回配置服务页面。"""
    target = web_dir() / name
    content = target.read_text(encoding=const.CHARSET, errors="replace")

    content = (
        content
        .replace("__APP_VERSION__", const.APP_VERSION)
        .replace("__APP_NAME__", const.APP_NAME)
        .replace("__APP_DESC__", const.APP_DESC)
    )

    return Response(content, media_type="text/html; charset=utf-8")


if __name__ == "__main__":
    pass
