# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import functools
import typing

from agent.domain.tool_policy import ToolFilterMode
from agent.ports.presentation import ApplicationView
from agent.ports.presentation import (
    StyledBlock,
    TextSpan
)
from frontends.helix.runtime import (
    ensure_service_runtime_asset,
    prepare_and_start_service_runtime,
)
from frontends.terminal.mcp_status import (
    McpStatusDetail,
    McpStatusView,
    inbuild_status_view,
    render_mcp_status_block,
)
from infrastructure.errors import AppError
from infrastructure.platform.file_assist import FileAssist
from infrastructure.services.runtime_context import ServiceRuntimeContext
from infrastructure.services.runtime_setup import service_runtime_asset_missing
from protocol.transport import config
from ..core.models import (
    FragmentBlock,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from ..core.styles import (
    ACCENT_STYLE,
    BODY_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    BRIGHT_STYLE,
    command_result_block,
    failure_text_block,
    fragment_block,
    interrupted_status_block
)

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost

DOWNLOAD_OPTIONS: tuple[tuple[bool, str, str], ...] = (
    (True, "Download MCP", "fetch and install Helix now"),
    (False, "Skip for now", "continue without Helix tools"),
)

TOOL_PROFILE_OPTIONS: tuple[tuple[ToolFilterMode, str, str], ...] = (
    ("app", "app", "Application automation, device, media, and performance tools"),
    ("api", "api", "API automation, security, and interface performance tools"),
)


def _present(
    host: "TuiApplicationHost",
    renderable: FragmentBlock | StyledBlock | None = None,
    *,
    view_type: str = "tui.command",
) -> None:
    """发送一项 Helix 功能展示。"""
    host.frontend.application.emit(ApplicationView(
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
    host: "TuiApplicationHost",
    *,
    state: str,
    error: str = ""
) -> None:
    """展示 Helix MCP 的最终状态。"""
    if state == "stopped":
        view = McpStatusView("Helix MCP stopped", "ready", True)
    elif state == "stop_failed":
        details = (
            (McpStatusDetail(f"  └ {error}", "failed"),)
            if error
            else ()
        )
        view = McpStatusView(
            "Helix MCP stop failed",
            "failed",
            True,
            details,
        )
    else:
        view = inbuild_status_view({
            "state": state,
            "label": "Helix MCP",
            "error": error,
        })

    block = render_mcp_status_block(
        view,
        terminal_width=host.frontend.application.viewport.width,
    )

    if not block.plain_text:
        return None
    _present(host, block, view_type="tui.helix.status")
    _present(host, view_type="tui.gap")


def _helix_error_detail(error: BaseException) -> str:
    """返回 Helix 操作失败时使用的简短详情。"""
    if isinstance(error, AppError):
        return str(error.message)

    message = str(error).strip()

    return (
        f"{type(error).__name__}: {message}"
        if message
        else type(error).__name__
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
        await self.runtime.hold_activity_status("download")


def render_helix_mode_result(host: "TuiApplicationHost", mode: ToolFilterMode) -> None:
    """展示工具过滤模式切换结果。"""
    _present(
        host,
        fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan("Helix tool filter set to ", BRIGHT_STYLE),
            TextSpan(mode, BRIGHT_STYLE),
        ),
        view_type="tui.helix.status",
    )
    _present(host, view_type="tui.gap")


def render_helix_download_result(host: "TuiApplicationHost", command: str) -> None:
    """展示运行时下载完成后的重新打开提示。"""
    _present(host, command_result_block(
        command,
        TextSpan("Downloaded", BRIGHT_STYLE),
        TextSpan(" · Reopen the app to continue", MUTED_STYLE),
    ))
    _present(host, view_type="tui.gap")


def render_helix_command_failure(
    host: "TuiApplicationHost",
    command: str,
    error: BaseException | str
) -> None:
    """展示 Helix 命令失败结果。"""
    detail = (
        _helix_error_detail(error)
        if isinstance(error, BaseException)
        else str(error).strip()
    )
    _present(
        host,
        command_result_block(
            command,
            TextSpan("Failed", FAILURE_STYLE),
            TextSpan(f" · {detail}", BODY_STYLE),
        ),
        view_type="tui.helix.status",
    )
    _present(host, view_type="tui.gap")


def render_helix_notice(
    host: "TuiApplicationHost",
    message: str
) -> None:
    """展示不带命令前缀的 Helix 普通状态提示。"""
    _present(
        host,
        fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan(str(message).strip(), BODY_STYLE),
        ),
        view_type="tui.helix.status",
    )
    _present(host, view_type="tui.gap")


def render_helix_link_result(host: "TuiApplicationHost", linked: bool) -> None:
    """展示 Helix 接入操作的最终结果。"""
    if not linked:
        return None

    _present_helix_result(host, state="ready")


def render_helix_link_failure(host: "TuiApplicationHost", error: BaseException) -> None:
    """展示 Helix 接入操作的失败结果。"""
    _present_helix_result(
        host,
        state="failed",
        error=_helix_error_detail(error),
    )


def render_helix_interrupted(
    host: "TuiApplicationHost",
    *,
    label: str = "Helix MCP"
) -> None:
    """展示 Helix 前台操作被用户中断的状态。"""
    _present(
        host,
        interrupted_status_block(label),
        view_type="tui.helix.interrupted",
    )
    _present(host, view_type="tui.gap")


def render_helix_stop_result(host: "TuiApplicationHost", _result: typing.Any = None) -> None:
    """展示 Helix 停止操作的成功结果。"""
    _present_helix_result(host, state="stopped")


def render_helix_stop_failure(host: "TuiApplicationHost", error: BaseException) -> None:
    """展示 Helix 停止操作的失败结果。"""
    _present_helix_result(
        host,
        state="stop_failed",
        error=_helix_error_detail(error),
    )


def render_helix_home_result(host: "TuiApplicationHost", url: str | None) -> None:
    """展示 Helix 首页操作的最终结果。"""
    if url is None:
        _present(host, _label_detail("Helix", "skipped"))
    else:
        _present(host, fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan(
                f"Opened {url} in your browser.",
                BRIGHT_STYLE,
            ),
        ))

    _present(host, view_type="tui.gap")


def render_helix_home_failure(host: "TuiApplicationHost", error: BaseException) -> None:
    """展示 Helix 首页操作的失败结果。"""
    detail = _helix_error_detail(error)
    _present(
        host,
        failure_text_block(
            f"Failed to open browser for {helix_runtime_home_url(host)}: "
            f"{detail}",
        ),
        view_type="tui.helix.status",
    )
    _present(host, view_type="tui.gap")


def helix_runtime_home_url(host: "TuiApplicationHost") -> str:
    """返回当前 Helix 服务管理器确认的首页地址。"""
    server_manager = host.service_runtime.manager

    url = str(server_manager.url or "").strip()

    return (url or config.BASE_URL).rstrip("/")


def unlink_helix_runtime(host: "TuiApplicationHost") -> None:
    """从当前工具会话移除 Helix MCP，不停止本地服务。"""
    was_linked = host.execution.is_service_linked()
    host.execution.unlink_service()

    if not was_linked:
        render_helix_notice(host, "Helix MCP already unlinked")
        return None

    render_helix_notice(host, "Helix MCP unlinked")


async def confirm_runtime_download(
    runtime: TuiRuntime,
    _context: ServiceRuntimeContext
) -> bool:
    """在主 TUI 中确认是否下载缺失的 Helix 运行时。"""
    result = await runtime.select_menu(MenuRequest(
        title="Helix Runtime Setup",
        view_id="helix:runtime-setup",
        status="Download the Helix runtime required for MCP tools.",
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(value=value, label=label, detail=detail)
            for value, label, detail in DOWNLOAD_OPTIONS
        ),
    ))
    return bool(result)


async def download_service_runtime(
    host: "TuiApplicationHost",
    context: ServiceRuntimeContext
) -> bool:
    """下载缺失的服务运行时，不启动服务或挂载工具。"""
    runtime = require_tui_runtime(host.frontend.runtime)
    return await ensure_service_runtime_asset(
        context,
        explicit_upgrade=False,
        progress=TuiUpgradeProgress(runtime),
    )


async def choose_helix_tool_profile(
    runtime: TuiRuntime,
    current: ToolFilterMode
) -> ToolFilterMode | None:
    """选择当前服务连接使用的工具过滤模式。"""
    selected = await runtime.select_menu(MenuRequest(
        title="Update Helix Tool Mode",
        view_id="helix:tool-mode",
        status="Choose the tool filter used by the connected Helix MCP.",
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(
                value=value,
                label=label,
                detail=detail,
                is_current=value == current,
            )
            for value, label, detail in TOOL_PROFILE_OPTIONS
        ),
        selected=next(
            index
            for index, (value, _label, _detail) in enumerate(
                TOOL_PROFILE_OPTIONS
            )
            if value == current
        ),
    ))
    if selected not in {"app", "api"}:
        return None
    return selected


async def open_helix_home(host: "TuiApplicationHost") -> str | None:
    """打开已经连接的服务管理首页。"""
    if not host.execution.is_service_linked():
        raise AppError("Helix MCP is not connected")

    url = helix_runtime_home_url(host)
    await FileAssist.open_url(url)
    return url


async def stop_helix_runtime(host: "TuiApplicationHost") -> None:
    """显示停止活动并关闭 Helix 服务。"""
    runtime = require_tui_runtime(host.frontend.runtime)
    if host.activity.enabled:
        await runtime.begin_operation_status(
            lambda: {"summary": "Helix MCP stopping"},
        )
    host.execution.unlink_service()
    await host.service_runtime.stop()


async def prepare_tui_service_runtime(
    host: "TuiApplicationHost",
    tool_profile: ToolFilterMode = "app",
    *,
    label: str = "Helix MCP",
    download_confirmed: bool = False
) -> bool:
    """通过当前 TUI 完成下载确认并启动 Helix 运行时。"""
    runtime = require_tui_runtime(host.frontend.runtime)

    return await prepare_and_start_service_runtime(
        host,
        tool_profile=tool_profile,
        label=label,
        confirm_download=functools.partial(confirm_runtime_download, runtime),
        progress=TuiUpgradeProgress(runtime),
        download_confirmed=download_confirmed,
        defer_activity_stop=True,
    )


async def confirm_tui_service_runtime_startup(host: "TuiApplicationHost") -> bool:
    """在后台准备开始前完成缺失运行时的下载确认。"""
    context = host.service_runtime.require_context()
    if not service_runtime_asset_missing(context):
        return True
    runtime = require_tui_runtime(host.frontend.runtime)
    return await confirm_runtime_download(runtime, context)


async def link_helix_runtime(
    host: "TuiApplicationHost",
    tool_profile: ToolFilterMode = "app",
    *,
    download_confirmed: bool = False
) -> bool:
    """确认本地服务已经启动，并挂载到当前工具会话。"""
    if host.execution.is_service_linked():
        host.execution.set_service_tool_profile(tool_profile)
        return True

    return await prepare_tui_service_runtime(
        host,
        tool_profile,
        download_confirmed=download_confirmed,
    )


async def finish_helix_activity(host: "TuiApplicationHost") -> None:
    """结束 Helix 前台操作占用的运行时活动区域。"""
    await host.activity.stop("inbuild", settle=False)


if __name__ == '__main__':
    pass
