# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.models import TextSpan, TextStyle
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from ..core.models import (
    MenuOption,
    MenuRequest
)
from mind_nova.requests.access import (
    access_mode_label,
    normalize_access_mode
)
from ..core.styles import BRIGHT_STYLE, MUTED_STYLE, fragment_block, text_block

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime


PERMISSION_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("safe", "Approval", "tool execution requires approval"),
    ("full", "Elevated", "tool execution may run without approval"),
)


async def choose_permissions_mode(
    runtime: "TuiRuntime",
    current_mode: typing.Any,
) -> str | None:
    """在主 TUI 中选择权限模式。"""
    current = normalize_access_mode(current_mode)
    return await runtime.select_menu(MenuRequest(
        title="Permissions",
        options=tuple(
            MenuOption(value=value, label=label, detail=detail)
            for value, label, detail in PERMISSION_OPTIONS
        ),
        selected=1 if current == "full" else 0,
    ))


def render_permissions_status(
    application: ApplicationSink,
    access_mode: typing.Any,
) -> None:
    """展示当前权限模式。"""
    normalized = normalize_access_mode(access_mode)
    label = access_mode_label(normalized)
    detail = (
        "tool execution requires approval"
        if normalized == "safe"
        else "tool execution may run without approval"
    )
    title_style = TextStyle(
        foreground="#D8B26E" if normalized == "full" else "#8FC7EA",
        bold=True,
    )
    application.emit(ApplicationView(
        type="tui.permissions.status",
        renderable=fragment_block(
            TextSpan("Permissions ", title_style),
            TextSpan(f"· {label}", BRIGHT_STYLE),
        ),
    ))
    application.emit(ApplicationView(
        type="tui.permissions.detail",
        renderable=text_block(f"└ {detail}", MUTED_STYLE),
    ))
    application.emit(ApplicationView(type="tui.gap"))


if __name__ == '__main__':
    pass
