# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
from agent.application import (
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
from metadata import const
from ..core.models import (
    MenuActionKind,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..core.styles import (
    fragment_block
)

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime

PermissionMenuValue = typing.Literal[
    "read-only",
    "auto",
    "ask-for-approval",
    "approve-for-me",
    "full-access",
]

PERMISSION_OPTIONS: tuple[tuple[PermissionMenuValue, str, str], ...] = (
    (
        "read-only",
        "Read Only",
        f"{const.APP_DESC} can read files in the current workspace. "
        "Approval is required to edit files or access the internet.",
    ),
    (
        "ask-for-approval",
        "Ask for approval",
        f"{const.APP_DESC} can read and edit files in the current workspace, "
        "and run commands. Approval is required to access the internet or edit "
        "other files.",
    ),
    (
        "approve-for-me",
        "Approve for me",
        "Only ask for actions detected as potentially unsafe.",
    ),
    (
        "full-access",
        "Full Access",
        f"{const.APP_DESC} can edit files outside this workspace and access the "
        "internet without asking for approval. Exercise caution when using.",
    ),
)


def _permission_settings(value: PermissionMenuValue) -> PermissionSettings:
    """返回菜单项对应的权限设置。"""
    if value == "read-only":
        return preset_permissions("read-only", display_label="Read Only")
    if value == "ask-for-approval":
        return preset_permissions("auto", approvals_reviewer="user")
    if value == "approve-for-me":
        return preset_permissions("auto", approvals_reviewer="auto_review")
    return preset_permissions("full-access", display_label="Full Access")


def _permission_detail() -> tuple[str, ...]:
    """生成权限确认面板的只读详情。"""
    return (
        f"When {const.APP_DESC} runs with full access, it can edit any file on "
        "your computer and run commands with network, without your approval.",
    )


def _permission_warning() -> str:
    """返回 Full Access 确认面板中的红色警示尾段。"""
    return (
        "Exercise caution when enabling full access. This significantly increases "
        "the risk of data loss, leaks, or unexpected behavior."
    )


def _permission_confirmation_menu(
    runtime: "TuiRuntime",
    settings: PermissionSettings
) -> MenuRequest:
    """生成权限预设的二级确认菜单。"""
    def cancel_confirmation() -> None:
        """关闭当前确认面板并返回权限列表。"""
        runtime.cancel_menu()

    return MenuRequest(
        title="Enable full access?",
        view_id=f"permissions:confirm:{settings.preset}",
        body=_permission_detail() + ("",),
        body_warning=_permission_warning(),
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=(
            MenuOption(
                value=settings,
                label="Yes, continue anyway",
                detail="Apply full access for this session",
            ),
            MenuOption(
                value=None,
                label="Cancel",
                detail="Go back without enabling full access",
                on_select=cancel_confirmation,
                dismiss_on_select=False,
            ),
        ),
    )


def _queue_permission_confirmation(
    runtime: "TuiRuntime",
    settings: PermissionSettings
) -> None:
    """把权限确认子菜单排入当前菜单会话。"""
    runtime.emit_menu_action(
        lambda: runtime.push_menu(
            _permission_confirmation_menu(runtime, settings),
        ),
        name="tui permissions navigation",
        kind=MenuActionKind.NAVIGATION,
    )


def _permission_menu_value(value: typing.Any) -> PermissionMenuValue | None:
    """验证菜单返回值是否为权限预设标识。"""
    if value == "read-only":
        return "read-only"
    if value == "auto":
        return "auto"
    if value == "ask-for-approval":
        return "ask-for-approval"
    if value == "approve-for-me":
        return "approve-for-me"
    if value == "full-access":
        return "full-access"
    return None


async def choose_permissions_mode(
    runtime: "TuiRuntime",
    current: PermissionSettings
) -> PermissionSettings | None:
    """在主 TUI 中选择权限模式。"""
    include_read_only = sys.platform == "win32"
    menu_options: list[tuple[PermissionMenuValue, str, str]] = []
    for option in PERMISSION_OPTIONS:
        if include_read_only or option[0] != "read-only":
            menu_options.append(option)

    def option_for(
        menu_value: PermissionMenuValue,
        option_label: str,
        option_detail: str
    ) -> MenuOption:
        """生成权限预设列表项及其必要的确认导航动作。"""
        settings = _permission_settings(menu_value)

        requires_confirmation = menu_value == "full-access"

        def open_confirmation() -> None:
            """打开 Full Access 的二次确认菜单。"""
            _queue_permission_confirmation(runtime, settings)

        return MenuOption(
            value=menu_value,
            label=option_label,
            detail=option_detail,
            on_select=(
                open_confirmation if requires_confirmation else None
            ),
            dismiss_on_select=not requires_confirmation,
            dismiss_parent_on_child_accept=requires_confirmation,
            is_current=settings == current,
        )

    options: list[MenuOption] = []
    selected_index: int       = 0

    for raw_value, label, detail in menu_options:
        value = _permission_menu_value(raw_value)
        if value is None:
            continue
        options.append(option_for(value, label, detail))
        if _permission_settings(value) == current:
            selected_index = len(options) - 1

    selected = await runtime.select_menu(MenuRequest(
        title="Update Model Permissions",
        view_id="permissions:root",
        status="Choose how model actions are approved.",
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(options),
        selected=selected_index,
    ))

    if selected is None:
        return None
    if isinstance(selected, PermissionSettings):
        return selected

    selected_value = _permission_menu_value(selected)
    if selected_value is None:
        return None
    return _permission_settings(selected_value)


def render_permissions_status(
    application: ApplicationSink,
    permissions: PermissionSettings
) -> None:
    """展示当前权限模式。"""
    label = permission_label(permissions)

    application.emit(ApplicationView(
        type="tui.permissions.status",
        renderable=fragment_block(
            TextSpan("• ", TextStyle(dim=True)),
            TextSpan(f"Permissions updated to {label}"),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))


if __name__ == '__main__':
    pass
