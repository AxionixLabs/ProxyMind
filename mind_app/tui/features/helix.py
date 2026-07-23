# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import functools
from engine.errors import MindError
from engine.file_assist import FileAssist
from mind_app.frontend import ApplicationView
from mind_app.presentation.mcp_status import render_mcp_status_block
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan
)
from mind_app.runtime.mcp.service_runtime import (
    ServiceRuntimeContext,
    prepare_and_start_service_runtime,
    service_runtime_asset_missing
)
from mind_core.mcp_status import inbuild_status_view
from mind_nova import const
from ..core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest
)
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from ..core.styles import (
    ACCENT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    WARNING_STYLE,
    fragment_block,
    text_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind

DOWNLOAD_OPTIONS: tuple[tuple[bool, str, str], ...] = (
    (True, "Download MCP", "fetch and install Helix now"),
    (False, "Skip for now", "continue without Helix tools"),
)


def _present(
    mind: "Mind",
    renderable: FragmentBlock | StyledBlock | None = None,
    *,
    view_type: str = "tui.command",
) -> None:
    """发送一项 Helix 功能展示。"""
    mind.frontend.application.emit(ApplicationView(
        type=view_type,
        renderable=renderable,
    ))


def _label_detail(label: str, detail: str) -> FragmentBlock:
    """生成标题和次要详情组成的 Helix 状态块。"""
    return fragment_block(
        TextSpan(f"{label} ", ACCENT_STYLE),
        TextSpan(f"· {detail}", MUTED_STYLE),
    )


def _present_helix_result(
    mind: "Mind",
    *,
    state: str,
    error: str = "",
) -> None:
    """展示 Helix MCP 的最终状态。"""
    view = inbuild_status_view({
        "state": state,
        "label": "Helix MCP",
        "error": error,
    })

    block = render_mcp_status_block(view)

    if not block.plain_text:
        return None
    _present(mind, block, view_type="tui.helix.status")
    _present(mind, view_type="tui.gap")


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
        help_text="Up/Down select · Enter choose · Esc/q cancel",
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
    download_confirmed: bool = False,
) -> bool:
    """通过当前 TUI 完成下载确认并启动 Helix 运行时。"""
    runtime = require_tui_runtime(mind.frontend.runtime)

    return await prepare_and_start_service_runtime(
        mind,
        label=label,
        confirm_download=functools.partial(confirm_runtime_download, runtime),
        progress=TuiUpgradeProgress(runtime),
        download_confirmed=download_confirmed,
    )


async def confirm_tui_service_runtime_startup(mind: "Mind") -> bool:
    """在后台准备开始前完成缺失运行时的下载确认。"""
    context = mind.require_service_runtime_context()
    if not service_runtime_asset_missing(context):
        return True
    runtime = require_tui_runtime(mind.frontend.runtime)
    return await confirm_runtime_download(runtime, context)


async def link_helix_runtime(mind: "Mind") -> None:
    """确认本地服务已经启动，并挂载到当前工具会话。"""
    try:
        helix_linked = await prepare_tui_service_runtime(mind)
    except MindError as error:
        _present_helix_result(mind, state="failed", error=str(error.message))
        return None
    except Exception as error:
        message = str(error).strip()

        detail = (
            f"{type(error).__name__}: {message}"
            if message
            else type(error).__name__
        )
        _present_helix_result(mind, state="failed", error=detail)
        return None

    if not helix_linked:
        _present(mind, _label_detail("Helix", "skipped"))
        _present(mind, view_type="tui.gap")
        return None

    _present_helix_result(mind, state="ready")


def unlink_helix_runtime(mind: "Mind") -> None:
    """从当前工具会话移除 Helix MCP，不停止本地服务。"""
    was_linked = mind.is_service_mcp_linked()
    mind.unlink_service_mcp()
    state = "unlinked" if was_linked else "already unlinked"
    _present(mind, _label_detail("Helix", state))
    _present(mind, view_type="tui.gap")


def render_helix_interrupted(
    mind: "Mind",
    *,
    label: str = "Helix MCP",
) -> None:
    """展示 Helix 前台操作被用户中断的状态。"""
    _present(
        mind,
        fragment_block(
            TextSpan(f"{label} ", ACCENT_STYLE),
            TextSpan("· interrupted", WARNING_STYLE),
        ),
        view_type="tui.helix.interrupted",
    )
    _present(mind, view_type="tui.gap")


async def open_helix_home(mind: "Mind") -> None:
    """启动或复用本地 Helix 服务，挂载 MCP 后打开首页。"""
    try:
        helix_ready = await prepare_tui_service_runtime(mind)
    except MindError as error:
        _present(mind, text_block(f"Helix home failed: {error}", FAILURE_STYLE))
        _present(mind, view_type="tui.gap")
        return None

    if not helix_ready:
        _present(mind, _label_detail("Helix", "skipped"))
        _present(mind, view_type="tui.gap")
        return None

    url = helix_runtime_home_url(mind)

    _present(mind, _label_detail("Helix Home", url))
    await FileAssist.open_url(url)
    _present(mind, view_type="tui.gap")


def helix_runtime_home_url(mind: "Mind") -> str:
    """返回当前 Helix 服务管理器确认的首页地址。"""
    server_manager = getattr(mind, "server_manager", None)

    url = str(getattr(server_manager, "url", "") or "").strip()

    return (url or const.BASE_URL).rstrip("/")


async def stop_helix_runtime(mind: "Mind") -> None:
    """停止 Helix 服务并打印结果。"""
    _present(mind, _label_detail("Helix", "stop"))

    try:
        await mind.stop_service_runtime()
    except MindError as error:
        _present(
            mind,
            text_block(f"Helix stop failed: {error}", FAILURE_STYLE),
        )
        _present(mind, view_type="tui.gap")
        return None

    _present(mind, view_type="tui.gap")


if __name__ == '__main__':
    pass
