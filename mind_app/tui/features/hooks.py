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
    body = (
        (f"Trust store: {catalog.trust_error}",)
        if catalog.trust_error
        else ()
    )

    return MenuRequest(
        title="Hooks",
        status=(
            f"installed={catalog.installed_count} "
            f"active={catalog.active_count}"
        ),
        body=body,
        help_text="Up/Down select | Enter inspect | Esc/q close",
        options=tuple(
            MenuOption(
                value=item.event,
                label=item.event,
                detail=(
                    f"installed={item.installed_count} "
                    f"active={item.active_count} | {item.description}"
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
                    f"matcher={item.matcher or '*'}"
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

    trusted = bool(action)
    try:
        updated = mind.set_hook_trust(
            entry.key,
            expected_content_hash=entry.content_hash,
            trusted=trusted,
            workspace=workspace,
        )
    except Exception as error:
        render_hooks_failure(mind.frontend.application, error)
        return _refresh_catalog(mind, workspace, catalog)

    render_hook_trust_status(
        mind.frontend.application,
        entry,
        trusted=trusted,
    )
    return updated


def hook_detail_menu(entry: HookCatalogEntry) -> MenuRequest:
    """生成单个 Hook 的详情和信任操作菜单。"""
    body = (
        f"Command: {entry.command}",
        f"Matcher: {entry.matcher or '*'}",
        f"Source: {entry.source_scope}",
        f"Path: {entry.source_path or '-'}",
        f"Enabled: {str(entry.enabled).lower()}",
        f"Trust: {entry.trust_state}",
        f"Active: {str(entry.active).lower()}",
        f"Timeout: {entry.timeout_sec:g}s",
        f"On error: {entry.on_error}",
        f"Content hash: {entry.content_hash[:12]}",
    )

    if entry.trust_state == "implicit":
        return MenuRequest(
            title="Hook Details",
            status=f"{entry.event} | implicit trust",
            body=body,
            help_text="Enter/Esc/q back",
        )

    trusted = entry.trust_state == "trusted"

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
                value=not trusted,
                label="Revoke trust" if trusted else "Trust hook",
                detail=(
                    "require approval before this hook can run"
                    if trusted
                    else "allow this exact hook content to run"
                ),
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


def render_hook_trust_status(
    application: ApplicationSink,
    entry: HookCatalogEntry,
    *,
    trusted: bool
) -> None:
    """展示 Hook 信任更新结果。"""
    application.emit(ApplicationView(
        type="tui.hooks.status",
        renderable=command_result_block(
            "/hooks",
            TextSpan("trusted" if trusted else "untrusted", BRIGHT_STYLE),
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
