# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from functools import partial

from agent.ports.mcp_runtime import (
    McpAction,
    McpAllServices,
    McpControlRequest,
    McpControlResult,
    McpRuntimeSnapshot,
    McpServiceSnapshot,
    McpServicesBusy,
    McpSingleService,
)
from agent.ports.presentation import (
    ApplicationView,
    StyledBlock,
    TextSpan,
)
from frontends.terminal.mcp_status import (
    McpStatusDetail,
    McpStatusView,
    external_mcp_status_view,
    render_mcp_status_block,
)
from frontends.terminal.text import sanitize_terminal_line
from frontends.terminal.text_layout import layout_styled_line
from infrastructure.mcp.external_status import external_status_detail_from_exception
from ..core.models import (
    CLOSE_MENU_FOOTER_HINT,
    FragmentBlock,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT,
)
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime,
)
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    fragment_block,
    interrupted_status_block,
)

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost

MCP_MENU_ACTIONS: tuple[tuple[McpAction, str], ...] = (
    ("start", "Start this service if enabled; keep an existing connection."),
    ("force", "Start this service even if disabled; keep its configuration and existing connection."),
    ("stop", "Disconnect this service. HTTP/SSE remote services stay running; stdio child processes close."),
    ("restart", "Validate config, disconnect this service, then start it only if enabled."),
    ("status", "View this service's connection and configuration without connecting."),
)
MCP_COMMAND_USAGE = "Usage: /mcp selects one service; /mcp <start|force|stop|restart|status> applies to all services."


def _present(
    host: "TuiApplicationHost",
    renderable: StyledBlock | FragmentBlock | None = None,
    *,
    view_type: str = "tui.mcp",
) -> None:
    """发送一项外部 MCP 展示。"""
    host.frontend.application.emit(ApplicationView(type=view_type, renderable=renderable))


def _mcp_terminal_width(host: "TuiApplicationHost") -> int:
    """返回 MCP 状态块使用的有效终端宽度。"""
    width = host.frontend.application.viewport.width
    return width if isinstance(width, int) and width > 0 else 120


def _present_external_mcp_result(host: "TuiApplicationHost", view: McpStatusView) -> bool:
    """通过共享渲染器提交一项最终状态。"""
    block = render_mcp_status_block(view, terminal_width=_mcp_terminal_width(host))
    if not block.plain_text:
        return False
    _present(host, block, view_type="tui.external_mcp.status")
    _present(host, view_type="tui.gap")
    return True


def parse_mcp_command(value: str) -> tuple[bool, McpAction | None]:
    """识别 MCP 命令并拒绝未知动作和多余参数，避免落入模型输入。"""
    parts = value.strip().casefold().split()
    if not parts or parts[0] != "/mcp":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2:
        for action, _ in MCP_MENU_ACTIONS:
            if parts[1] == action:
                return True, action
    raise ValueError(MCP_COMMAND_USAGE)


def all_mcp_request(host: "TuiApplicationHost", action: McpAction) -> McpControlRequest:
    """为直接命令冻结当前实例身份和显式全量目标。"""
    snapshot = host.execution.external_mcp.snapshot
    return McpControlRequest(snapshot.runtime_id, snapshot.workspace, action, McpAllServices())


def _scope_label(request: McpControlRequest) -> str:
    """生成展示范围，原始配置键只在展示边界清理控制字符。"""
    if isinstance(request.target, McpSingleService):
        return sanitize_terminal_line(request.target.config_key)
    return "all services"


def _config_label(service: McpServiceSnapshot) -> str:
    """独立表达配置事实与禁用服务临时连接的语义。"""
    if service.config_enabled is None:
        return "removed from config"
    if service.config_enabled:
        return "enabled"
    return "disabled (temporary connection)" if service.state == "ready" else "disabled"


def _service_detail(service: McpServiceSnapshot) -> str:
    """提供菜单与结果共用的连接和配置摘要。"""
    return f"{service.state} · {_config_label(service)} · {service.transport} · {len(service.tools)} tools"


def render_mcp_action_result(host: "TuiApplicationHost", result: McpControlResult) -> None:
    """根据逐服务结论展示一次操作结果，不把已有连接计为本次启动。"""
    failures = sum(item.outcome == "failed" for item in result.services)
    busy = any(item.outcome == "busy" for item in result.services)
    details = tuple(
        McpStatusDetail(
            f"  {item.config_key}: {item.outcome}"
            + (f" · {_service_detail(item.snapshot)}" if item.snapshot is not None else "")
            + (f" · {item.operation_error}" if item.operation_error else ""),
            "failed" if item.outcome == "failed" else "warning" if item.outcome == "busy" else "",
        )
        for item in result.services
    )
    if not details:
        details = (McpStatusDetail("  No MCP services configured or connected."),)
    suffix = "failed" if failures else "busy" if busy else "complete"
    _present_external_mcp_result(host, McpStatusView(
        summary=f"External MCP · {_scope_label(result.request)} · {result.request.action} {suffix}",
        level="failed" if failures else "warning" if busy else "ready",
        done=True,
        details=details,
    ))


def render_mcp_action_failure(
    host: "TuiApplicationHost", request: McpControlRequest, error: BaseException,
) -> None:
    """展示失败范围，忙碌拒绝不改变连接事实。"""
    busy = isinstance(error, McpServicesBusy)
    _present_external_mcp_result(host, McpStatusView(
        summary=f"External MCP · {_scope_label(request)} · {request.action} {'busy' if busy else 'failed'}",
        level="warning" if busy else "failed",
        done=True,
        details=(McpStatusDetail(external_status_detail_from_exception(error), "warning" if busy else "failed"),),
    ))


def render_mcp_unavailable(host: "TuiApplicationHost", error: RuntimeError) -> None:
    """实例不可用时提交本地错误，不打开菜单或创建控制请求。"""
    _present_external_mcp_result(host, McpStatusView(
        summary="External MCP unavailable", level="failed", done=True,
        details=(McpStatusDetail(external_status_detail_from_exception(error), "failed"),),
    ))


def render_mcp_action_cancelled(host: "TuiApplicationHost", request: McpControlRequest) -> None:
    """在生命周期清理完成后展示取消的操作范围。"""
    _present(
        host,
        interrupted_status_block(f"External MCP · {_scope_label(request)}", action=request.action),
        view_type="tui.external_mcp.interrupted",
    )
    _present(host, view_type="tui.gap")


def render_external_mcp_start_status(
    host: "TuiApplicationHost", *, error: BaseException | None = None,
) -> bool:
    """展示应用启动阶段最近一次外部 MCP 接入的最终状态。"""
    if error is not None:
        view = McpStatusView(
            summary="External MCP failed", level="failed", done=True,
            details=(McpStatusDetail(f"  └ {external_status_detail_from_exception(error)}", "failed"),),
        )
    else:
        runtime = host.execution.external_mcp.current
        snapshot = runtime.last_start_snapshot if runtime is not None else {}
        if not snapshot:
            return False
        view = external_mcp_status_view(snapshot, detail_limit=5)
    return _present_external_mcp_result(host, view)


def render_mcp_status(host: "TuiApplicationHost", request: McpControlRequest | None = None) -> None:
    """仅读取本地类型化快照，展示连接事实和配置错误。"""
    runtime = host.execution.external_mcp
    try:
        snapshot = runtime.snapshot
    except RuntimeError as error:
        if request is None:
            render_mcp_unavailable(host, error)
        else:
            render_mcp_action_failure(host, request, error)
        return
    if request is None:
        request = McpControlRequest(snapshot.runtime_id, snapshot.workspace, "status", McpAllServices())
    if request.runtime_id != snapshot.runtime_id or request.workspace != snapshot.workspace:
        render_mcp_action_failure(host, request, RuntimeError("MCP runtime or workspace is no longer active"))
        return
    services = snapshot.services
    if isinstance(request.target, McpSingleService):
        key = request.target.config_key
        services = tuple(item for item in services if item.config_key == key)
        if not services:
            render_mcp_action_failure(host, request, RuntimeError("MCP service target no longer exists"))
            return
    lines = [TextSpan(f"External MCP · {_scope_label(request)} · status", BRIGHT_STYLE)]
    if snapshot.config_error:
        lines.append(TextSpan(f"Config error: {snapshot.config_error}", FAILURE_STYLE))
    if not services:
        lines.append(TextSpan("No MCP services configured or connected.", MUTED_STYLE))
    for service in sorted(services, key=lambda item: item.config_key):
        names = tuple(name.removeprefix(service.tool_prefix) for name in service.tools)
        lines.extend((
            TextSpan(service.config_key, BRIGHT_STYLE),
            TextSpan(f"Connection: {service.state}", BODY_STYLE),
            TextSpan(f"Config: {_config_label(service)}", BODY_STYLE),
            TextSpan(f"Transport: {service.transport}", BODY_STYLE),
            TextSpan(f"Tools ({len(names)}): {', '.join(names) or '(none)'}", MUTED_STYLE),
            TextSpan(f"Discovered: {service.discovered} · Filtered: {service.filtered}", MUTED_STYLE),
        ))
        if service.connection_error:
            lines.append(TextSpan(f"Connection error: {service.connection_error}", FAILURE_STYLE))
    parts: list[TextSpan] = []
    for index, line in enumerate(lines):
        if index:
            parts.append(TextSpan("\n"))
        parts.extend(layout_styled_line(
            [TextSpan(sanitize_terminal_line(line.text), line.style)],
            terminal_width=_mcp_terminal_width(host),
            hard=True,
        ))
    _present(host, fragment_block(*parts))
    _present(host, view_type="tui.gap")


def _push_service_actions(
    runtime: TuiRuntime, snapshot: McpRuntimeSnapshot, service: McpServiceSnapshot,
) -> None:
    """把冻结的原始配置键带入共享菜单栈，返回时保留父级选择和滚动位置。"""
    target = McpSingleService(service.config_key)
    runtime.push_menu(MenuRequest(
        title=f"External MCP · {sanitize_terminal_line(service.config_key)}",
        view_id="mcp:service",
        status=_service_detail(service),
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        selected=4,
        options=tuple(
            MenuOption(
                value=McpControlRequest(snapshot.runtime_id, snapshot.workspace, action, target),
                label=action,
                detail=(
                    "Disconnect this service; the remote server keeps running."
                    if action == "stop" and service.transport != "stdio"
                    else "Disconnect this service and close its child processes."
                    if action == "stop"
                    else detail
                ),
            )
            for action, detail in MCP_MENU_ACTIONS
        ),
    ))


async def choose_mcp_action(runtime: TuiRuntime, host: "TuiApplicationHost") -> McpControlRequest | None:
    """选择单服务操作；取消与空菜单不会产生全量请求。"""
    snapshot = host.execution.external_mcp.snapshot
    services = sorted(snapshot.services, key=lambda item: (sanitize_terminal_line(item.config_key), item.config_key))
    body = (sanitize_terminal_line(snapshot.config_error),) if snapshot.config_error else ()
    if not services:
        body += ("No MCP services configured or connected.",)
    selected = await runtime.select_menu(MenuRequest(
        title="External MCP",
        view_id="mcp:root",
        status="Select one service. Direct /mcp <action> commands apply to all services.",
        body=body,
        body_wrap=True,
        footer_hint=STANDARD_MENU_FOOTER_HINT if services else CLOSE_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(
                value=McpSingleService(service.config_key),
                label=sanitize_terminal_line(service.config_key),
                detail=_service_detail(service),
                on_select=partial(_push_service_actions, runtime, snapshot, service),
                dismiss_on_select=False,
                dismiss_parent_on_child_accept=True,
            )
            for service in services
        ),
    ))
    if isinstance(selected, McpControlRequest) and isinstance(selected.target, McpSingleService):
        return selected
    return None


async def finish_mcp_activity(host: "TuiApplicationHost", action: McpAction) -> None:
    """结束应用启动或操作对应的活动状态。"""
    if action == "stop":
        runtime = require_tui_runtime(host.frontend.runtime)
        await runtime.end_activity_status("operation", settle=False)
    else:
        await host.activity.stop("external_mcp", settle=False)


async def run_mcp_action(host: "TuiApplicationHost", request: McpControlRequest) -> McpControlResult:
    """执行冻结目标的统一控制请求，复用前台屏障的活动交接。"""
    if host.activity.enabled and request.action in ("stop", "restart"):
        runtime = require_tui_runtime(host.frontend.runtime)
        label = f"External MCP · {_scope_label(request)} · {'stopping' if request.action == 'stop' else 'restarting'}"
        if request.action == "stop":
            await runtime.begin_operation_status(lambda: {"summary": label})
        else:
            await runtime.begin_external_mcp_status(lambda: {"summary": label, "done": False, "items": []})
    return await host.execution.external_mcp.control(request, defer_activity_stop=True)


if __name__ == '__main__':
    pass
