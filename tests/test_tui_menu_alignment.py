# -*- coding: utf-8 -*-

import asyncio

import pytest

from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.menu import TuiMenu
from mind_app.tui.core.models import MenuOption, MenuRequest


@pytest.mark.anyio
async def test_mcp_option_details_align_to_restart_width() -> None:
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
    )
    request = MenuRequest(
        title="External MCP",
        options=(
            MenuOption("start", "start", "启动服务"),
            MenuOption("force", "force", "强制启动"),
            MenuOption("stop", "stop", "停止服务"),
            MenuOption("restart", "restart", "重启服务"),
            MenuOption("status", "status", "查看状态"),
        ),
    )

    task = asyncio.create_task(menu.request(request))
    await asyncio.sleep(0)
    lines = _fragments_text(menu.fragments()).splitlines()[2:]
    separator_columns = [
        get_cwidth(line.split(" · ", 1)[0])
        for line in lines
    ]
    menu.finish(None)
    await task

    assert len(set(separator_columns)) == 1


def _fragments_text(parts) -> str:
    return "".join(text for _, text in parts)
