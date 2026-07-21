# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import functools
from mind_app.runtime.mcp.service_runtime import (
    ServiceRuntimeContext,
    prepare_and_start_service_runtime
)

from ..core.models import (
    MenuOption,
    MenuRequest
)
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)

if typing.TYPE_CHECKING:
    from ...controller import Mind


DOWNLOAD_OPTIONS: tuple[tuple[bool, str, str], ...] = (
    (True, "Download MCP", "fetch and install Helix now"),
    (False, "Skip for now", "continue without Helix tools"),
)


class TuiUpgradeProgress(object):
    """把运行时下载进度映射到 TUI 动画区域。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self.runtime = runtime

    async def start(self, state: dict[str, typing.Any]) -> None:
        """启动主 TUI 中的运行时下载状态。"""
        await self.runtime.begin_download_status(lambda: dict(state))

    async def stop(self) -> None:
        """停止主 TUI 中的运行时下载状态。"""
        await self.runtime.end_activity_status("download")


async def confirm_runtime_download(
    runtime: TuiRuntime,
    context: ServiceRuntimeContext,
) -> bool:
    """在主 TUI 中确认是否下载缺失的 Helix 运行时。"""
    result = await runtime.select_menu(MenuRequest(
        title="Helix Runtime Setup",
        status=context.app_desc,
        options=tuple(
            MenuOption(value=value, label=label, detail=detail)
            for value, label, detail in DOWNLOAD_OPTIONS
        ),
    ))
    return bool(result)


async def prepare_tui_service_runtime(
    mind: "Mind",
    *,
    label: str = "Helix MCP",
) -> bool:
    """通过当前 TUI 完成下载确认并启动 Helix 运行时。"""
    runtime = require_tui_runtime(mind.frontend.runtime)

    return await prepare_and_start_service_runtime(
        mind,
        label=label,
        confirm_download=functools.partial(confirm_runtime_download, runtime),
        progress=TuiUpgradeProgress(runtime),
    )


if __name__ == '__main__':
    pass
