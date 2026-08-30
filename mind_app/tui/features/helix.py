# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import functools
from infrastructure.errors import AppError
from infrastructure.platform.file_assist import FileAssist
from mind_app.presentation.application import ApplicationView
from mind_app.presentation.mcp_status import (
    McpStatusDetail,
    McpStatusView,
    inbuild_status_view,
    render_mcp_status_block,
)
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan
)
from mind_app.runtime.mcp.service_runtime import (
    ServiceRuntimeContext,
    ensure_service_runtime_asset,
    prepare_and_start_service_runtime,
    service_runtime_asset_missing
)
from mind_app.runtime.tools.mode_policy import ToolFilterMode
from metadata import const
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
    from ...controller import Mind

DOWNLOAD_OPTIONS: tuple[tuple[bool, str, str], ...] = (
    (True, "Download MCP", "fetch and install Helix now"),
    (False, "Skip for now", "continue without Helix tools"),
)

TOOL_PROFILE_OPTIONS: tuple[tuple[ToolFilterMode, str, str], ...] = (
    ("app", "app", "Application automation, device, media, and performance tools"),
    ("api", "api", "API automation, security, and interface performance tools"),
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
        terminal_width=getattr(
            getattr(mind.frontend.application, "viewport", None),
            "width",
            None,
        ),
    )

    if not block.plain_text:
        return None
    _present(mind, block, view_type="tui.helix.status")
    _present(mind, view_type="tui.gap")


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


def render_helix_mode_result(mind: "Mind", mode: ToolFilterMode) -> None:
    """展示工具过滤模式切换结果。"""
    _present(
        mind,
        fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan("Helix tool filter set to ", BRIGHT_STYLE),
            TextSpan(mode, BRIGHT_STYLE),
        ),
        view_type="tui.helix.status",
    )
    _present(mind, view_type="tui.gap")


def render_helix_download_result(mind: "Mind", command: str) -> None:
    """展示运行时下载完成后的重新打开提示。"""
    _present(mind, command_result_block(
        command,
        TextSpan("Downloaded", BRIGHT_STYLE),
        TextSpan(" · Reopen the app to continue", MUTED_STYLE),
    ))
    _present(mind, view_type="tui.gap")


def render_helix_command_failure(
    mind: "Mind",
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
        mind,
        command_result_block(
            command,
            TextSpan("Failed", FAILURE_STYLE),
            TextSpan(f" · {detail}", BODY_STYLE),
        ),
        view_type="tui.helix.status",
    )
    _present(mind, view_type="tui.gap")


def render_helix_notice(
    mind: "Mind",
    message: str
) -> None:
    """展示不带命令前缀的 Helix 普通状态提示。"""
    _present(
        mind,
        fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan(str(message).strip(), BODY_STYLE),
        ),
        view_type="tui.helix.status",
    )
    _present(mind, view_type="tui.gap")


def render_helix_link_result(mind: "Mind", linked: bool) -> None:
    """展示 Helix 接入操作的最终结果。"""
    if not linked:
        return None

    _present_helix_result(mind, state="ready")


def render_helix_link_failure(mind: "Mind", error: BaseException) -> None:
    """展示 Helix 接入操作的失败结果。"""
    _present_helix_result(
        mind,
        state="failed",
        error=_helix_error_detail(error),
    )


def render_helix_interrupted(
    mind: "Mind",
    *,
    label: str = "Helix MCP"
) -> None:
    """展示 Helix 前台操作被用户中断的状态。"""
    _present(
        mind,
        interrupted_status_block(label),
        view_type="tui.helix.interrupted",
    )
    _present(mind, view_type="tui.gap")


def render_helix_stop_result(mind: "Mind", _result: typing.Any = None) -> None:
    """展示 Helix 停止操作的成功结果。"""
    _present_helix_result(mind, state="stopped")


def render_helix_stop_failure(mind: "Mind", error: BaseException) -> None:
    """展示 Helix 停止操作的失败结果。"""
    _present_helix_result(
        mind,
        state="stop_failed",
        error=_helix_error_detail(error),
    )


def render_helix_home_result(mind: "Mind", url: str | None) -> None:
    """展示 Helix 首页操作的最终结果。"""
    if url is None:
        _present(mind, _label_detail("Helix", "skipped"))
    else:
        _present(mind, fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan(
                f"Opened {url} in your browser.",
                BRIGHT_STYLE,
            ),
        ))

    _present(mind, view_type="tui.gap")


def render_helix_home_failure(mind: "Mind", error: BaseException) -> None:
    """展示 Helix 首页操作的失败结果。"""
    detail = _helix_error_detail(error)
    _present(
        mind,
        failure_text_block(
            f"Failed to open browser for {helix_runtime_home_url(mind)}: "
            f"{detail}",
        ),
        view_type="tui.helix.status",
    )
    _present(mind, view_type="tui.gap")


def helix_runtime_home_url(mind: "Mind") -> str:
    """返回当前 Helix 服务管理器确认的首页地址。"""
    server_manager = mind.service_runtime.manager

    url = str(getattr(server_manager, "url", "") or "").strip()

    return (url or const.BASE_URL).rstrip("/")


def unlink_helix_runtime(mind: "Mind") -> None:
    """从当前工具会话移除 Helix MCP，不停止本地服务。"""
    was_linked = mind.is_service_mcp_linked()
    mind.unlink_service_mcp()

    if not was_linked:
        render_helix_notice(mind, "Helix MCP already unlinked")
        return None

    render_helix_notice(mind, "Helix MCP unlinked")


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
    mind: "Mind",
    context: ServiceRuntimeContext
) -> bool:
    """下载缺失的服务运行时，不启动服务或挂载工具。"""
    runtime = require_tui_runtime(mind.frontend.runtime)
    return await ensure_service_runtime_asset(
        context,
        explicit_upgrade=False,
        anim_manager=mind.anim_manager,
        design=mind.design,
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


async def open_helix_home(mind: "Mind") -> str | None:
    """打开已经连接的服务管理首页。"""
    if not mind.is_service_mcp_linked():
        raise AppError("Helix MCP is not connected")

    url = helix_runtime_home_url(mind)
    await FileAssist.open_url(url)
    return url


async def stop_helix_runtime(mind: "Mind") -> None:
    """显示停止活动并关闭 Helix 服务。"""
    runtime = require_tui_runtime(mind.frontend.runtime)
    if bool(getattr(mind, "animate", True)):
        await runtime.begin_operation_status(
            lambda: {"summary": "Helix MCP stopping"},
        )
    mind.unlink_service_mcp()
    await mind.service_runtime.stop()


async def prepare_tui_service_runtime(
    mind: "Mind",
    tool_profile: ToolFilterMode = "app",
    *,
    label: str = "Helix MCP",
    download_confirmed: bool = False
) -> bool:
    """通过当前 TUI 完成下载确认并启动 Helix 运行时。"""
    runtime = require_tui_runtime(mind.frontend.runtime)

    return await prepare_and_start_service_runtime(
        mind,
        tool_profile=tool_profile,
        label=label,
        confirm_download=functools.partial(confirm_runtime_download, runtime),
        progress=TuiUpgradeProgress(runtime),
        download_confirmed=download_confirmed,
        defer_activity_stop=True,
    )


async def confirm_tui_service_runtime_startup(mind: "Mind") -> bool:
    """在后台准备开始前完成缺失运行时的下载确认。"""
    context = mind.service_runtime.require_context()
    if not service_runtime_asset_missing(context):
        return True
    runtime = require_tui_runtime(mind.frontend.runtime)
    return await confirm_runtime_download(runtime, context)


async def link_helix_runtime(
    mind: "Mind",
    tool_profile: ToolFilterMode = "app",
    *,
    download_confirmed: bool = False
) -> bool:
    """确认本地服务已经启动，并挂载到当前工具会话。"""
    if mind.is_service_mcp_linked():
        mind.set_service_tool_profile(tool_profile)
        return True

    return await prepare_tui_service_runtime(
        mind,
        tool_profile,
        download_confirmed=download_confirmed,
    )


async def finish_helix_activity(mind: "Mind") -> None:
    """结束 Helix 前台操作占用的运行时活动区域。"""
    await mind.stop_anim("inbuild", settle=False)


if __name__ == '__main__':
    pass
