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
    MenuOption,
    MenuRequest
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
    "PreToolUse"        : "工具执行前",
    "PermissionRequest" : "请求工具权限时",
    "PostToolUse"       : "工具执行后",
    "PreCompact"        : "上下文压缩前",
    "PostCompact"       : "上下文压缩后",
    "SessionStart"      : "新会话启动时",
    "UserPromptSubmit"  : "用户提交提示词时",
    "SubagentStart"     : "子代理创建时",
    "SubagentStop"      : "子代理结束当前轮次前",
    "Stop"              : "当前轮次结束前",
    "SessionEnd"        : "根会话结束时",
}

_TOOL_EVENT_COVERAGE = {
    "PreToolUse"        : "client-full/server-approval-only",
    "PermissionRequest" : "approval-events",
    "PostToolUse"       : "client-only",
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

    while True:
        event = await runtime.select_menu(hook_event_menu(catalog))
        if event is None:
            return None
        catalog = await _manage_hook_event(
            runtime,
            mind,
            workspace,
            catalog,
            str(event),
        )


def hook_event_menu(catalog: HookCatalogSnapshot) -> MenuRequest:
    """生成 Hook 事件汇总菜单。"""
    installed_width = max(
        (len(str(item.installed_count)) for item in catalog.events),
        default=1,
    )
    active_width = max(
        (len(str(item.active_count)) for item in catalog.events),
        default=1,
    )

    return MenuRequest(
        title="Hooks",
        status=(
            f"installed={catalog.installed_count} "
            f"active={catalog.active_count}"
        ),
        body=tuple(
            f"Warning: {warning}"
            for warning in catalog.warnings
        ),
        help_text="Up/Down select | Enter inspect | Esc/q close",
        options=tuple(
            MenuOption(
                value=item.event,
                label=item.event,
                detail=(
                    f"installed={item.installed_count:>{installed_width}} "
                    f"active={item.active_count:>{active_width}} | "
                    f"{item.control_policy} | "
                    f"{_coverage_summary(item.event)}"
                    f"match={item.matcher_subject or '-'} | "
                    f"{_EVENT_DESCRIPTIONS.get(item.event, item.description)}"
                ),
            )
            for item in catalog.events
        ),
    )


async def _manage_hook_event(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    event: str
) -> HookCatalogSnapshot:
    """管理指定事件下的 Hook 条目。"""
    while True:
        selected_key = await runtime.select_menu(
            hook_list_menu(catalog, event)
        )
        if selected_key is None:
            return catalog

        entry = next(
            (
                item
                for item in catalog.hooks
                if item.event == event and item.key == selected_key
            ),
            None,
        )
        if entry is None:
            return _refresh_catalog(mind, workspace, catalog)

        catalog = await _manage_hook_entry(
            runtime,
            mind,
            workspace,
            catalog,
            entry,
        )


def hook_list_menu(
    catalog: HookCatalogSnapshot,
    event: str
) -> MenuRequest:
    """生成指定事件的 Hook 条目菜单。"""
    hooks = tuple(
        item for item in catalog.hooks
        if item.event == event
    )

    return MenuRequest(
        title=event,
        status=(
            f"installed={len(hooks)} "
            f"active={sum(item.active for item in hooks)}"
        ),
        body=(f"No hooks installed for {event}.",) if not hooks else (),
        help_text=(
            "Up/Down select | Enter inspect | Esc/q back"
            if hooks
            else "Enter/Esc/q back"
        ),
        options=tuple(
            MenuOption(
                value=item.key,
                label=item.command,
                detail=(
                    f"{_hook_state(item)} | {item.source_scope} | "
                    f"{_matcher_summary(item)}"
                ),
            )
            for item in hooks
        ),
    )


async def _manage_hook_entry(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    entry: HookCatalogEntry
) -> HookCatalogSnapshot:
    """显示 Hook 详情并按需更新信任。"""
    action = await runtime.select_menu(hook_detail_menu(entry))
    if action is None or action is _BACK_ACTION:
        return catalog

    try:
        if action == _TRUST_ACTION:
            updated = mind.trust_hook(
                entry.key,
                expected_content_hash=entry.content_hash,
                workspace=workspace,
            )
        else:
            updated = mind.set_hook_enabled(
                entry.key,
                expected_content_hash=entry.content_hash,
                enabled=action == _ENABLE_ACTION,
                workspace=workspace,
            )
    except Exception as error:
        render_hooks_failure(mind.frontend.application, error)
        return _refresh_catalog(mind, workspace, catalog)

    render_hook_state_status(
        mind.frontend.application,
        entry,
        action=str(action),
    )
    return updated


def hook_detail_menu(entry: HookCatalogEntry) -> MenuRequest:
    """生成单个 Hook 的详情和信任操作菜单。"""
    body = (
        f"Command: {entry.command}",
        f"Windows command: {entry.command_windows or '-'}",
        f"Status message: {entry.status_message or '-'}",
        _matcher_detail(entry),
        f"Coverage: {_TOOL_EVENT_COVERAGE.get(entry.event, 'lifecycle')}",
        f"Source: {entry.source_scope}",
        f"Path: {entry.source_path or '-'}",
        f"Trust: {entry.trust_state}",
        f"Enabled: {str(entry.enabled).lower()}",
        f"Active: {str(entry.active).lower()}",
        f"Timeout: {entry.timeout_sec:g}s",
        f"Async: {str(entry.run_async).lower()}",
        f"Additional context limit: {entry.additional_context_limit}",
        f"Content hash: {entry.content_hash[:12]}",
    )

    if entry.trust_state == "managed":
        return MenuRequest(
            title="Hook Details",
            status=f"{entry.event} | managed",
            body=body,
            help_text="Enter/Esc/q back",
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
        status=f"{entry.event} | {_hook_state(entry)}",
        body=body,
        selected=0,
        help_text="Up/Down select | Enter apply | Esc/q back",
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
            ),
        ),
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


def _coverage_summary(event: str) -> str:
    """返回工具事件的执行位置覆盖摘要。"""
    coverage = _TOOL_EVENT_COVERAGE.get(event)
    return f"coverage={coverage} | " if coverage else ""


def _matcher_summary(entry: HookCatalogEntry) -> str:
    """返回带匹配对象的简短匹配规则。"""
    if entry.matcher_subject is None:
        return "matcher=-"
    return f"matcher[{entry.matcher_subject}]={entry.matcher or '*'}"


def _matcher_detail(entry: HookCatalogEntry) -> str:
    """返回带匹配对象的匹配规则详情。"""
    if entry.matcher_subject is None:
        return "Matcher: -"
    return f"Matcher ({entry.matcher_subject}): {entry.matcher or '*'}"


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
