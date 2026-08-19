# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from mind_app.presentation.models import TextSpan
from mind_app.runtime.hooks.catalog import (
    HookCatalogEntry,
    HookCatalogSnapshot
)
from ..core.models import (
    CLOSE_MENU_FOOTER_HINT,
    MenuActionKind,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    command_result_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..core.runtime import TuiRuntime

_BACK_ACTION = object()

_TRUST_ACTION   = "trust"
_ENABLE_ACTION  = "enable"
_DISABLE_ACTION = "disable"

_EVENT_DESCRIPTIONS = {
    "PreToolUse": "工具执行前",
    "PermissionRequest": "请求工具权限时",
    "PostToolUse": "工具执行后",
    "PreCompact": "上下文压缩前",
    "PostCompact": "上下文压缩后",
    "SessionStart": "新会话启动时",
    "UserPromptSubmit": "用户提交提示词时",
    "SubagentStart": "子代理创建时",
    "SubagentStop": "子代理结束当前轮次前",
    "Stop": "当前轮次结束前",
    "SessionEnd": "根会话结束时",
}


async def manage_hooks(
    runtime: "TuiRuntime",
    mind: "Mind"
) -> None:
    """在主 TUI 中查看 Hook 并管理显式信任。"""
    workspace = Path(mind.history_workspace)
    try:
        catalog = mind.inspect_hooks(workspace=workspace)
    except Exception as error:
        render_hooks_failure(mind.frontend.application, error)
        return None

    await runtime.select_menu(
        _hook_root_request(runtime, mind, workspace, catalog)
    )


async def _run_hook_action(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    entry: HookCatalogEntry,
    event: str,
    action: typing.Any
) -> None:
    """执行 Hook 变更并按稳定标识刷新仍在栈中的菜单。"""
    session_id = runtime.active_menu_session_id()
    if session_id is None:
        return None

    try:
        if action == _TRUST_ACTION:
            mind.trust_hook(
                entry.key,
                expected_content_hash=entry.content_hash,
                workspace=workspace,
            )
        else:
            mind.set_hook_enabled(
                entry.key,
                expected_content_hash=entry.content_hash,
                enabled=action == _ENABLE_ACTION,
                workspace=workspace,
            )
        render_hook_state_status(
            mind.frontend.application,
            entry,
            action=str(action),
        )
        refreshed = _refresh_catalog(mind, workspace, catalog)
        if runtime.menu_session_is_active(session_id):
            runtime.replace_present_menu_if_id(
                "hooks:events",
                _hook_root_request(runtime, mind, workspace, refreshed),
            )
            runtime.replace_present_menu_if_id(
                f"hooks:list:{event}",
                _hook_list_request(
                    runtime,
                    mind,
                    workspace,
                    refreshed,
                    event,
                ),
            )
    except Exception as error:
        if runtime.menu_session_is_active(session_id):
            runtime.push_menu(hook_failure_panel(entry, error))

def _hook_root_request(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot
) -> MenuRequest:
    """生成带有事件导航回调的 Hook 根菜单。"""
    return hook_event_menu(
        catalog,
        on_event=lambda event: _push_hook_list(
            runtime,
            mind,
            workspace,
            catalog,
            event,
        ),
    )


def _push_hook_list(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    event: str
) -> None:
    """压入指定事件的 Hook 列表。"""
    runtime.emit_menu_action(
        lambda: runtime.push_menu(_hook_list_request(
            runtime,
            mind,
            workspace,
            catalog,
            event,
        )),
        name="tui hook navigation",
        kind=MenuActionKind.NAVIGATION,
    )


def _hook_list_request(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    event: str
) -> MenuRequest:
    """生成带有详情导航回调的 Hook 列表。"""

    def open_hook_detail(entry: HookCatalogEntry) -> None:
        """压入条目详情及其操作回调。"""

        def start_hook_action(action: typing.Any) -> None:
            """将条目操作排入菜单事件队列。"""

            def run_hook_action() -> None:
                """启动由界面生命周期管理的条目操作。"""
                runtime.start_background_task(
                    _run_hook_action(
                        runtime,
                        mind,
                        workspace,
                        catalog,
                        entry,
                        event,
                        action,
                    ),
                    name="tui hook menu action",
                )

            runtime.emit_menu_action(
                run_hook_action,
                name="tui hook menu action",
                kind=MenuActionKind.DOMAIN,
            )

        def push_hook_detail() -> None:
            """压入当前条目的详情菜单。"""
            runtime.push_menu(hook_detail_menu(
                entry,
                on_action=start_hook_action,
            ))

        runtime.emit_menu_action(
            push_hook_detail,
            name="tui hook navigation",
            kind=MenuActionKind.NAVIGATION,
        )

    return hook_list_menu(
        catalog,
        event,
        on_entry=open_hook_detail,
    )


def hook_event_menu(
    catalog: HookCatalogSnapshot,
    *,
    on_event: typing.Callable[[str], None] | None = None
) -> MenuRequest:
    """生成 Hook 事件汇总菜单。"""
    return MenuRequest(
        title="Hooks",
        view_id="hooks:events",
        status=(
            f"installed={catalog.installed_count} "
            f"active={catalog.active_count}"
        ),
        body=tuple(
            f"Warning: {warning}"
            for warning in catalog.warnings
        ),
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(
                value=item.event,
                label=item.event,
                detail=(
                    f"{item.active_count}/{item.installed_count} active · "
                    f"{_EVENT_DESCRIPTIONS.get(item.event, item.description)}"
                ),
                on_select=(
                    lambda selected=item.event: on_event(selected)
                    if on_event is not None
                    else None
                ),
                dismiss_on_select=on_event is None,
            )
            for item in catalog.events
        ),
    )


def hook_list_menu(
    catalog: HookCatalogSnapshot,
    event: str,
    *,
    on_entry: typing.Callable[[HookCatalogEntry], None] | None = None
) -> MenuRequest:
    """生成指定事件的 Hook 条目菜单。"""
    hooks = tuple(
        item for item in catalog.hooks
        if item.event == event
    )

    return MenuRequest(
        title=event,
        view_id=f"hooks:list:{event}",
        status=(
            f"installed={len(hooks)} "
            f"active={sum(item.active for item in hooks)}"
        ),
        body=(f"No hooks installed for {event}.",) if not hooks else (),
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(
                value=item.key,
                label=item.command,
                detail=(
                    f"{_hook_state(item)} | {item.source_scope} | "
                    f"{_matcher_summary(item)}"
                ),
                on_select=(
                    lambda selected=item: on_entry(selected)
                    if on_entry is not None
                    else None
                ),
                dismiss_on_select=on_entry is None,
            )
            for item in hooks
        ),
    )


def hook_detail_menu(
    entry: HookCatalogEntry,
    *,
    on_action: typing.Callable[[typing.Any], None] | None = None
) -> MenuRequest:
    """生成单个 Hook 的详情和信任操作菜单。"""
    body = _hook_detail_body(entry)

    if entry.trust_policy == "managed":
        return MenuRequest(
            title="Hook Details",
            view_id=f"hooks:detail:{entry.key}",
            status=f"{entry.event} | managed",
            body=body,
            help_text="",
            footer_hint=CLOSE_MENU_FOOTER_HINT,
        )

    if entry.trust_state == "trusted":
        action = _DISABLE_ACTION if entry.enabled else _ENABLE_ACTION
        label  = "Disable hook" if entry.enabled else "Enable hook"

        detail = (
            "keep trust but prevent this hook from running"
            if entry.enabled
            else "allow this trusted hook to run"
        )

    else:
        action = _TRUST_ACTION
        label  = "Trust hook"
        detail = "allow this exact hook content to run"

    return MenuRequest(
        title="Hook Details",
        view_id=f"hooks:detail:{entry.key}",
        status=f"{entry.event} | {_hook_state(entry)}",
        body=body,
        selected=0,
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=(
            MenuOption(
                value=_BACK_ACTION,
                label="Back",
                detail="keep the current trust state",
            ),
            MenuOption(
                value=action,
                label=label,
                detail=detail,
                on_select=(
                    lambda: on_action(action)
                    if on_action is not None
                    else None
                ),
            ),
        ),
    )


def hook_failure_panel(
    entry: HookCatalogEntry,
    error: BaseException
) -> MenuRequest:
    """生成 Hook 操作失败的只读子面板。"""
    message = str(error).strip() or type(error).__name__
    return MenuRequest(
        title="Hook operation",
        view_id=f"hooks:failure:{entry.key}",
        status=entry.command,
        body=(f"Failed: {message}",),
        help_text="",
        footer_hint=CLOSE_MENU_FOOTER_HINT,
    )


def _refresh_catalog(
    mind: "Mind",
    workspace: Path,
    fallback: HookCatalogSnapshot
) -> HookCatalogSnapshot:
    """重新读取 Hook 清单，失败时保留已有快照。"""
    try:
        return mind.inspect_hooks(workspace=workspace)
    except Exception as error:
        render_hooks_failure(mind.frontend.application, error)
        return fallback


def _hook_state(entry: HookCatalogEntry) -> str:
    """返回 Hook 的简短运行状态。"""
    if entry.active:
        return "active"
    if not entry.enabled:
        return "disabled"
    return entry.trust_state


def _matcher_summary(entry: HookCatalogEntry) -> str:
    """返回带匹配对象的简短匹配规则。"""
    if entry.matcher_subject is None:
        return "matcher=-"
    return f"matcher[{entry.matcher_subject}]={entry.matcher or '*'}"


def _hook_detail_body(entry: HookCatalogEntry) -> tuple[str, ...]:
    """生成审查和控制 Hook 所需的紧凑详情。"""
    lines = [f"Command: {entry.command}"]

    if entry.command_windows:
        lines.append(f"Windows command: {entry.command_windows}")
    if entry.status_message:
        lines.append(f"Message: {entry.status_message}")
    if entry.matcher_subject is not None:
        lines.append(f"Matcher: {entry.matcher or '*'}")

    source = entry.source_scope

    if entry.source_path:
        source = f"{source} · {entry.source_path}"
    lines.extend((
        f"Source: {source}",
        f"Timeout: {entry.timeout_sec:g}s",
    ))

    return tuple(lines)


def render_hook_state_status(
    application: ApplicationSink,
    entry: HookCatalogEntry,
    *,
    action: str
) -> None:
    """展示 Hook 信任或启用状态的更新结果。"""
    status = {
        _TRUST_ACTION: "trusted",
        _ENABLE_ACTION: "enabled",
        _DISABLE_ACTION: "disabled",
    }.get(action, action)

    application.emit(ApplicationView(
        type="tui.hooks.status",
        renderable=command_result_block(
            "/hooks",
            TextSpan(status, BRIGHT_STYLE),
            TextSpan(" · ", MUTED_STYLE),
            TextSpan(entry.command, BODY_STYLE),
        ),
    ))

    application.emit(ApplicationView(type="tui.gap"))


def render_hooks_failure(
    application: ApplicationSink,
    error: BaseException
) -> None:
    """展示 Hook 管理失败状态。"""
    message = str(error).strip() or type(error).__name__

    application.emit(ApplicationView(
        type="tui.hooks.failure",
        renderable=command_result_block(
            "/hooks",
            TextSpan("Failed", FAILURE_STYLE),
            TextSpan(f" · {message}", BODY_STYLE),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))


if __name__ == '__main__':
    pass
