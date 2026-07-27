# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.permissions import (
    PermissionPreset,
    PermissionSettings,
    permission_label,
    preset_permissions
)
from mind_app.presentation.models import (
    TextSpan,
    TextStyle
)
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from ..core.models import (
    MenuOption,
    MenuRequest
)
from ..core.styles import (
    BRIGHT_STYLE,
    MUTED_STYLE,
    fragment_block
)

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime

PERMISSION_OPTIONS: tuple[tuple[PermissionPreset, str, str], ...] = (
    ("read-only", "Read Only", "inspect files and ask before broader actions"),
    ("auto", "Auto", "work in the workspace and ask before crossing its boundary"),
    ("full-access", "Full Access", "run without sandbox restrictions or approval prompts"),
)


async def choose_permissions_mode(
    runtime: "TuiRuntime",
    current: PermissionSettings
) -> PermissionSettings | None:
    """在主 TUI 中选择权限模式。"""
    selected = await runtime.select_menu(MenuRequest(
        title="Permissions",
        status=f"current={permission_label(current)}",
        options=tuple(
            MenuOption(value=value, label=label, detail=detail)
            for value, label, detail in PERMISSION_OPTIONS
        ),
        selected=next(
            (
                index
                for index, (value, _label, _detail) in enumerate(PERMISSION_OPTIONS)
                if value == current.preset
            ),
            0,
        ),
    ))

    if selected is None:
        return None

    return preset_permissions(typing.cast(PermissionPreset, selected))


def render_permissions_status(
    application: ApplicationSink,
    permissions: PermissionSettings
) -> None:
    """展示当前权限模式。"""
    label = permission_label(permissions)

    detail = (
        f"sandbox={permissions.sandbox_mode}"
        f" · approval={permissions.approval_policy}"
    )

    title_style = TextStyle(
        foreground=(
            "#D8B26E"
            if permissions.sandbox_mode == "danger-full-access"
            else "#8FC7EA"
        ),
        bold=True,
    )

    application.emit(ApplicationView(
        type="tui.permissions.status",
        renderable=fragment_block(
            TextSpan("Permissions ", title_style),
            TextSpan(f"· {label}", BRIGHT_STYLE),
            TextSpan(f" · {detail}", MUTED_STYLE),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))


if __name__ == '__main__':
    pass
