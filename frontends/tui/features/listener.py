# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.ports.presentation import ApplicationView
from agent.ports.presentation import TextSpan
from frontends.terminal.mcp_status import (
    McpStatusDetail,
    McpStatusView,
    render_mcp_status_block,
)
from infrastructure.errors import AppError
from ..core.models import (
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
    BRIGHT_STYLE,
    COMMAND_STYLE,
    MUTED_STYLE,
    fragment_block,
    interrupted_status_block
)

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost

ListenerAction = typing.Literal["start", "stop", "status"]
ListenerOperation = typing.Literal["start", "stop"]

ListenerOutcome = typing.Literal[
    "ready",
    "already_ready",
    "stopped",
    "already_stopped",
]

_LISTENER_ACTIONS: typing.Final[frozenset[str]] = frozenset({
    "start",
    "stop",
    "status",
})

_LISTENER_MENU_OPTIONS: typing.Final[tuple[MenuOption, ...]] = (
    MenuOption(
        value="start",
        label="Start listener",
        detail="Connect and wait for the server to become ready.",
    ),
    MenuOption(
        value="stop",
        label="Stop listener",
        detail="Stop transport and keep pending messages in this process.",
    ),
)


def parse_listener_command(
    value: str
) -> tuple[bool, ListenerAction | None]:
    """解析监听命令及其可选动作。"""
    parts = str(value or "").strip().casefold().split()
    if not parts or parts[0] != "/listen":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2 and parts[1] in _LISTENER_ACTIONS:
        return True, typing.cast(ListenerAction, parts[1])
    return False, None


def render_listener_result(
    controller: "TuiApplicationHost",
    outcome: ListenerOutcome
) -> None:
    """把监听器操作结果写入稳定正文。"""
    summary = {
        "ready": "Listener ready",
        "already_ready": "Listener already ready",
        "stopped": "Listener stopped",
        "already_stopped": "Listener already stopped",
    }[outcome]

    _present_listener_view(
        controller,
        McpStatusView(summary=summary, level="ready", done=True),
    )


def render_listener_status(controller: "TuiApplicationHost") -> None:
    """把当前监听器状态作为命令查询结果写入稳定正文。"""
    listener = controller.subscription.current
    running = listener is not None and listener.is_running()
    ready = listener is not None and listener.is_ready()
    pending = listener.inbox.pending_count() if listener is not None else 0
    state = "listening" if ready else "connecting" if running else "stopped"

    block = fragment_block(
        TextSpan("/listen status", COMMAND_STYLE),
        TextSpan("\n\n"),
        TextSpan("Listener", BRIGHT_STYLE),
        TextSpan("\n\n"),
        TextSpan("  • ", ACCENT_STYLE),
        TextSpan("Status: ", BODY_STYLE),
        TextSpan(state, MUTED_STYLE),
        TextSpan(" · Pending: ", BODY_STYLE),
        TextSpan(str(pending), MUTED_STYLE),
    )
    controller.frontend.application.emit(ApplicationView(
        type="tui.listener.status",
        renderable=block,
    ))
    controller.frontend.application.emit(ApplicationView(type="tui.gap"))


def render_listener_failure(
    controller: "TuiApplicationHost",
    action: ListenerOperation,
    error: BaseException
) -> None:
    """把监听器操作失败结果写入稳定正文。"""
    summary = "Listener failed" if action == "start" else "Listener stop failed"
    detail = _listener_error_detail(error)

    _present_listener_view(
        controller,
        McpStatusView(
            summary=summary,
            level="failed",
            done=True,
            details=(McpStatusDetail(f"  └ {detail}", "failed"),),
        ),
    )


def render_listener_interrupted(
    controller: "TuiApplicationHost",
    action: ListenerOperation
) -> None:
    """把监听器操作中断结果写入稳定正文。"""
    controller.frontend.application.emit(ApplicationView(
        type="tui.listener.interrupted",
        renderable=interrupted_status_block("Listener", action=action),
    ))
    controller.frontend.application.emit(ApplicationView(type="tui.gap"))


def _present_listener_view(
    controller: "TuiApplicationHost",
    view: McpStatusView
) -> None:
    """提交一项监听器最终状态。"""
    block = render_mcp_status_block(
        view,
        terminal_width=controller.frontend.application.viewport.width,
    )
    if not block.plain_text:
        return None
    controller.frontend.application.emit(ApplicationView(
        type="tui.listener.status",
        renderable=block,
    ))
    controller.frontend.application.emit(ApplicationView(type="tui.gap"))


def _listener_error_detail(error: BaseException) -> str:
    """返回监听器操作失败时使用的简短详情。"""
    if isinstance(error, AppError):
        return str(error.message)

    message = str(error).strip()

    return (
        f"{type(error).__name__}: {message}"
        if message
        else type(error).__name__
    )


async def _begin_listener_activity(
    controller: "TuiApplicationHost",
    summary: str
) -> None:
    """按当前动画设置启动单行监听器操作状态。"""
    if not controller.activity.enabled:
        return None
    runtime = require_tui_runtime(controller.frontend.runtime)
    await runtime.begin_operation_status(lambda: {"summary": summary})


async def choose_listener_action(
    runtime: TuiRuntime,
    controller: "TuiApplicationHost"
) -> ListenerOperation | None:
    """在主 TUI 中选择监听器启动或停止操作。"""
    listener = controller.subscription.current

    selected = await runtime.select_menu(MenuRequest(
        title="Update Listener",
        view_id="listener:root",
        status="Start or stop the remote request listener.",
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=_LISTENER_MENU_OPTIONS,
        selected=1 if listener is not None and listener.is_running() else 0,
    ))

    if selected not in {"start", "stop"}:
        return None
    return selected


async def run_listener_action(
    controller: "TuiApplicationHost",
    action: ListenerOperation
) -> ListenerOutcome:
    """执行监听器启动或停止操作，并返回稳定结果状态。"""
    listener = controller.subscription.current

    if action == "start":
        if listener is not None and listener.is_ready():
            return "already_ready"

        await _begin_listener_activity(controller, "Listener starting")
        listener = controller.subscription.start()
        try:
            await listener.wait_until_ready()
        except TimeoutError:
            await controller.lifecycle.await_cleanup(
                controller.subscription.pause()
            )
            raise

        return "ready"

    if listener is None or not listener.is_running():
        return "already_stopped"

    await _begin_listener_activity(controller, "Listener stopping")
    await controller.subscription.pause()

    return "stopped"


if __name__ == '__main__':
    pass
