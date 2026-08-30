# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from infrastructure.config.schema import ConfigValidationError
from infrastructure.config.store import ConfigStoreError
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
    MenuColumnWidthMode,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..core.styles import (
    BODY_STYLE,
    FAILURE_STYLE,
    command_result_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..core.runtime import TuiRuntime

_TRUST_ACTION     = "trust"
_ENABLE_ACTION    = "enable"
_DISABLE_ACTION   = "disable"
_STARTUP_REVIEW   = "review"
_STARTUP_CONTINUE = "continue"


def _trust_startup_hooks(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot
) -> None:
    """按当前内容哈希信任启动审核菜单中的全部 Hook。"""
    session_id = runtime.active_menu_session_id()
    if session_id is None:
        return None

    runtime.update_menu(_startup_hooks_review_request(
        runtime,
        mind,
        workspace,
        catalog,
        trusting_all=True,
    ))
    runtime.start_background_task(
        _run_startup_hook_trust(
            runtime,
            mind,
            workspace,
            catalog,
            session_id=session_id,
        ),
        name="tui startup hook trust",
    )


def _startup_hooks_review_request(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    *,
    error: str = "",
    trusting_all: bool = False
) -> MenuRequest:
    """绑定启动审核菜单的批量信任操作。"""
    return startup_hooks_review_menu(
        catalog,
        error=error,
        trusting_all=trusting_all,
        on_trust=lambda: _trust_startup_hooks(
            runtime,
            mind,
            workspace,
            catalog,
        ),
    )


def _queue_batch_trust(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot
) -> None:
    """把 L1 批量信任动作交给菜单生命周期管理。"""
    session_id = runtime.active_menu_session_id()
    runtime.start_background_task(
        _run_batch_trust(
            runtime,
            mind,
            workspace,
            catalog,
            session_id=session_id,
        ),
        name="tui hook batch trust",
    )


def _refresh_catalog(
    mind: "Mind",
    workspace: Path,
    fallback: HookCatalogSnapshot,
    *,
    runtime: "TuiRuntime",
    session_id: int,
) -> HookCatalogSnapshot:
    """重新读取 Hook 清单，失败时保留已有快照。"""
    try:
        return mind.inspect_hooks(workspace=workspace)
    except (ConfigStoreError, ConfigValidationError) as error:
        if runtime.menu_session_is_active(session_id):
            render_hooks_failure(mind.frontend.application, error)
        return fallback


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


def _detail_line(label: str, value: str) -> str:
    """生成对齐的详情行。"""
    return f"{label:<{max(10, len(label) + 1)}}{value}"


def _close_hooks_browser(runtime: "TuiRuntime") -> bool:
    """关闭 Hooks 浏览器的全部菜单层并恢复输入焦点。"""
    return runtime.dismiss_menu_by_id(
        "hooks:events",
        session_id=runtime.active_menu_session_id(),
    )


def _review_needed_message(count: int) -> str | None:
    """返回待审核 Hook 的提示文本。"""
    if count <= 0:
        return None
    if count == 1:
        return "1 hook needs review before it can run."
    return f"{count} hooks need review before they can run."


def _handler_detail_fields(entry: HookCatalogEntry) -> tuple[tuple[str, str], ...]:
    """返回按处理器类型排列的详情字段。"""
    if entry.handler_type == "command":
        return (
            ("Command", entry.command or ""),
            ("Mode", "Async" if entry.run_async else "Sync"),
        )
    if entry.handler_type == "mcp_tool":
        return (
            ("MCP Server", entry.mcp_server or ""),
            ("MCP Tool", entry.mcp_tool or ""),
        )
    return (("Handler", entry.handler_type.title()),)


def _handler_summary(entry: HookCatalogEntry) -> str:
    """返回处理器的单行摘要。"""
    if entry.handler_type == "command":
        return entry.command or "Command"
    if entry.handler_type == "mcp_tool":
        return f"{entry.mcp_server or ''}/{entry.mcp_tool or ''}"
    return entry.handler_type.title()


def _hook_list_request(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    event: str
) -> MenuRequest:
    """生成带有选中详情和动作回调的 Hook 列表。"""

    def run_hook_action(entry: HookCatalogEntry, action: str) -> None:
        """把指定 Hook 的状态操作交给后台任务。"""
        if action == _TRUST_ACTION and not entry.needs_review:
            return None
        if action != _TRUST_ACTION and not entry.toggleable:
            return None

        def start_action() -> None:
            session_id = runtime.active_menu_session_id()
            runtime.start_background_task(
                _run_hook_action(
                    runtime,
                    mind,
                    workspace,
                    catalog,
                    entry,
                    event,
                    action,
                    session_id=session_id,
                ),
                name="tui hook menu action",
            )

        runtime.emit_menu_action(
            start_action,
            name="tui hook menu action",
            kind=MenuActionKind.DOMAIN,
        )

    def selected_hook() -> HookCatalogEntry | None:
        """返回当前菜单中选中的 Hook。"""
        state = runtime.screen.menu.state
        if state is None or not 0 <= state.selected < len(state.request.options):
            return None
        selected_key = state.request.options[state.selected].value
        return next(
            (item for item in catalog.hooks if item.key == selected_key),
            None,
        )

    def toggle_entry(entry: HookCatalogEntry) -> None:
        """请求切换指定 Hook；不可切换状态由统一动作守卫忽略。"""
        run_hook_action(
            entry,
            _ENABLE_ACTION if not entry.enabled else _DISABLE_ACTION,
        )

    def toggle_selected() -> None:
        """切换当前选中且已审核 Hook 的启用状态。"""
        entry = selected_hook()
        if entry is None:
            return None
        toggle_entry(entry)

    def enter_selected(entry: HookCatalogEntry) -> None:
        """处理 L2 的 Enter，并保持详情区留在当前列表页。"""
        toggle_entry(entry)

    return hook_list_menu(
        catalog,
        event,
        on_entry=enter_selected,
        on_toggle=toggle_selected,
        on_trust=lambda: (
            run_hook_action(entry, _TRUST_ACTION)
            if (entry := selected_hook()) is not None
            else None
        ),
        on_ctrl_c=lambda: _close_hooks_browser(runtime),
    )


def _hook_row_label(entry: HookCatalogEntry, index: int) -> str:
    """返回 Hook 列表行。"""
    marker = "!" if entry.needs_review else ("x" if entry.computed_active else " ")
    suffix = (
        " · modified" if entry.trust_state == "modified"
        else " · new" if entry.trust_state == "untrusted"
        else ""
    )
    return f"[{marker}] Hook {index + 1}{suffix}"


def _hook_list_footer(entry: HookCatalogEntry | None) -> str:
    """返回当前选中 Hook 对应的动态 footer。"""
    if entry is None:
        return "Press esc to go back"
    if entry.trust_policy == "managed":
        return "Managed hooks are always on; press esc to go back"
    if entry.needs_review:
        return "Press t to trust; esc to go back"
    return "Press space or enter to toggle; esc to go back"


def _hook_detail_body(entry: HookCatalogEntry) -> tuple[str, ...]:
    """按固定字段顺序生成 Hook 详情。"""
    return tuple(
        _detail_line(label, value)
        for label, value in _hook_detail_fields(entry)
    )


def _hook_detail_fields(entry: HookCatalogEntry) -> tuple[tuple[str, str], ...]:
    """返回按固定顺序排列的详情字段。"""
    fields: list[tuple[str, str]] = [("Event", entry.event)]
    if entry.matcher:
        fields.append(("Matcher", entry.matcher))

    fields.append(("Source", _hook_source_label(entry)))
    fields.extend(_handler_detail_fields(entry))
    fields.append(("Timeout", f"{entry.timeout_sec:g}s"))

    if entry.additional_context_limit is not None:
        context = (
            "unlimited" if entry.additional_context_limit == 0
            else f"limit: {entry.additional_context_limit} approximate tokens"
        )
        fields.append(("Context", context))
    fields.append(("Trust", _hook_trust_label(entry)))

    return tuple(fields)


def _hook_detail_fragments(entry: HookCatalogEntry) -> tuple[tuple[tuple[str, str], ...], ...]:
    """生成详情字段的标签和值样式片段。"""
    return tuple(
        (
            (
                "class:tui-menu.label",
                f"{label:<{max(10, len(label) + 1)}}",
            ),
            ("class:tui-menu.detail", value),
        )
        for label, value in _hook_detail_fields(entry)
    )


def _hook_detail_line_limits(entry: HookCatalogEntry) -> tuple[int | None, ...]:
    """返回详情字段的换行上限；命令字段最多显示三行。"""
    return tuple(
        3 if label == "Command" else None
        for label, _value in _hook_detail_fields(entry)
    )


def _hook_source_label(entry: HookCatalogEntry) -> str:
    """返回来源标签及其可选路径。"""
    label = {
        "system": "Admin config",
        "mdm": "Admin config",
        "user": "User config",
        "profile": "User config",
        "project": "Project config",
        "session flags": "Session flags",
        "cli": "Session flags",
        "plugin": "Plugin",
        "cloud-managed": "Cloud-managed config",
    }.get(entry.source_scope, "Unknown source")

    if entry.source_path:
        return f"{label} - {_display_source_path(entry.source_path)}"
    return label


def _display_source_path(source_path: str) -> str:
    """按统一规则把用户目录下的来源路径压缩为波浪号路径。"""
    path = Path(source_path)

    try:
        relative = path.resolve().relative_to(Path.home().resolve())
    except (OSError, ValueError):
        return str(path)
    if not relative.parts:
        return "~"

    return str(Path("~", *relative.parts))


def _hook_trust_label(entry: HookCatalogEntry) -> str:
    """返回信任状态标签。"""
    return {
        "managed": "Managed",
        "trusted": "Trusted",
        "modified": "Modified since last trusted - review required",
        "untrusted": "New hook - review required",
    }[entry.trust_state]


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
        on_trust=lambda: _queue_batch_trust(
            runtime,
            mind,
            workspace,
            catalog,
        ),
        on_ctrl_c=lambda: _close_hooks_browser(runtime),
    )


def hook_event_menu(
    catalog: HookCatalogSnapshot,
    *,
    on_event: typing.Callable[[str], None] | None = None,
    on_trust: typing.Callable[[], None] | None = None,
    on_ctrl_c: typing.Callable[[], bool] | None = None
) -> MenuRequest:
    """生成 Hook 事件汇总菜单。"""
    review_count = sum(item.needs_review for item in catalog.hooks)
    show_review  = review_count > 0

    selected = next(
        (
            index
            for index, item in enumerate(catalog.events)
            if item.review_count > 0
        ),
        0,
    )

    footer = (
        "Press t to trust all; enter to review hooks; esc to close"
        if show_review
        else "Press enter to view hooks; esc to close"
    )

    body: list[str]        = [""]
    body_styles: list[str] = [""]

    review_message = _review_needed_message(review_count)
    if review_message is not None:
        body.extend((f"⚠ {review_message}", ""))
        body_styles.extend(("class:tui-menu.review", ""))
    if catalog.warnings:
        body.append("Issues")
        body_styles.append("class:tui-menu.body.heading")
        body.extend(f"⚠ {warning}" for warning in catalog.warnings)
        body_styles.extend("class:tui-menu.body" for _ in catalog.warnings)
        body.append("")
        body_styles.append("")

    columns = ("Event", "Installed", "Active")
    widths  = (22, 12, 12)

    if show_review:
        columns += ("Review",)
        widths += (12,)
    header = "".join(
        f"{value:<{width}}"
        for value, width in zip(columns, widths)
    ) + "Description"
    body.append(header)
    body_styles.append("")

    return MenuRequest(
        title="Hooks",
        view_id="hooks:events",
        selected=selected,
        status="Lifecycle hooks from config and enabled plugins.",
        body=tuple(body),
        help_text="",
        footer_hint=footer,
        description_layout=MenuDescriptionLayout.COLUMNS,
        column_width_mode=MenuColumnWidthMode.FIXED,
        name_column_width=22,
        description_separator="",
        table_column_widths=(22, 12, 12, 12, 0) if show_review else (22, 12, 12, 0),
        options=tuple(
            MenuOption(
                value=item.event,
                label=item.event,
                detail="",
                columns=(
                    (item.event, str(item.installed_count), str(item.active_count),
                     str(item.review_count),
                     item.description)
                    if show_review
                    else (item.event, str(item.installed_count), str(item.active_count),
                          item.description)
                ),
                column_styles=(
                    ("class:tui-menu.label", "class:tui-menu.detail",
                     "class:tui-menu.detail", "class:tui-menu.review",
                     "class:tui-menu.detail")
                    if show_review and item.review_count > 0
                    else ("class:tui-menu.label", "class:tui-menu.detail",
                          "class:tui-menu.detail", "class:tui-menu.detail",
                          "class:tui-menu.detail")
                    if show_review
                    else ("class:tui-menu.label", "class:tui-menu.detail",
                          "class:tui-menu.detail", "class:tui-menu.detail")
                ),
                on_select=(
                    lambda event_value=item.event: on_event(event_value)
                    if on_event is not None
                    else None
                ),
                dismiss_on_select=on_event is None,
            )
            for item in catalog.events
        ),
        on_t=on_trust if show_review else None,
        show_option_gutter=False,
        show_all_options=True,
        body_as_table_header=True,
        body_preserve_spacing=True,
        body_styles=tuple(body_styles),
        on_ctrl_c=on_ctrl_c,
    )


def hook_list_menu(
    catalog: HookCatalogSnapshot,
    event: str,
    *,
    on_entry: typing.Callable[[HookCatalogEntry], None] | None = None,
    on_toggle: typing.Callable[[], None] | None = None,
    on_trust: typing.Callable[[], None] | None = None,
    on_ctrl_c: typing.Callable[[], bool] | None = None
) -> MenuRequest:
    """生成指定事件的 Hook 条目菜单。"""
    hooks = tuple(
        item
        for item in sorted(
            catalog.hooks,
            key=lambda candidate: candidate.display_order,
        )
        if item.event == event
    )

    review_count = sum(item.needs_review for item in hooks)

    return MenuRequest(
        title=f"{event} hooks",
        view_id=f"hooks:list:{event}",
        status=(
            _review_needed_message(review_count)
            or "Turn hooks on or off. Your changes are saved automatically."
        ),
        status_style=(
            "class:tui-menu.review" if review_count else ""
        ),
        body=("", "No hooks installed for this event.") if not hooks else (),
        body_styles=("", "class:tui-menu.body.empty") if not hooks else (),
        help_text="",
        footer_hint=_hook_list_footer(hooks[0] if hooks else None),
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(
                value=item.key,
                label=_hook_row_label(item, index),
                detail="",
                selected_body=_hook_detail_body(item),
                selected_body_fragments=_hook_detail_fragments(item),
                selected_body_line_limits=_hook_detail_line_limits(item),
                selected_footer_hint=_hook_list_footer(item),
                row_style=(
                    "class:tui-menu.review" if item.needs_review
                    else "class:tui-menu.detail"
                    if item.trust_policy == "managed"
                    else ""
                ),
                selected_row_style=(
                    "class:tui-menu.review-selected" if item.needs_review
                    else ""
                ),
                on_select=(
                    lambda selected=item: on_entry(selected)
                    if on_entry is not None
                    else None
                ),
                dismiss_on_select=on_entry is None,
            )
            for index, item in enumerate(hooks)
        ),
        on_space=on_toggle,
        on_t=on_trust,
        show_option_gutter=False,
        body_preserve_spacing=True,
        body_wrap=bool(hooks),
        on_ctrl_c=on_ctrl_c,
    )


def hook_failure_panel(
    entry: HookCatalogEntry,
    error: BaseException,
    *,
    on_ctrl_c: typing.Callable[[], bool] | None = None
) -> MenuRequest:
    """生成 Hook 操作失败的只读子面板。"""
    message = str(error).strip() or type(error).__name__

    return MenuRequest(
        title="Hook operation",
        view_id=f"hooks:failure:{entry.key}",
        status=_handler_summary(entry),
        body=(f"Failed: {message}",),
        help_text="",
        footer_hint=CLOSE_MENU_FOOTER_HINT,
        on_ctrl_c=on_ctrl_c,
    )


def startup_hooks_review_menu(
    catalog: HookCatalogSnapshot,
    *,
    on_trust: typing.Callable[[], None] | None = None,
    error: str = "",
    trusting_all: bool = False
) -> MenuRequest:
    """生成启动阶段的 Hook 审核选择菜单。"""
    review_count = sum(item.needs_review for item in catalog.hooks)

    count_message = (
        "1 hook is new or changed."
        if review_count == 1
        else f"{review_count} hooks are new or changed."
    )

    body        = ["Hooks can run outside the sandbox after you trust them."]
    body_styles = ["class:tui-menu.detail"]

    if trusting_all:
        body.append("Trusting hooks...")
        body_styles.append("class:tui-menu.detail")
    if error:
        body.append(error)
        body_styles.append("class:tui-menu.error")

    return MenuRequest(
        title="Hooks need review",
        view_id="hooks:startup-review",
        status=count_message,
        status_style="class:tui-menu.review",
        body=tuple(body),
        body_styles=tuple(body_styles),
        options=(
            MenuOption(
                value=_STARTUP_REVIEW,
                label="Review hooks",
                disabled=trusting_all,
            ),
            MenuOption(
                value=_STARTUP_CONTINUE,
                label="Trust all and continue",
                on_select=on_trust,
                dismiss_on_select=on_trust is None,
                disabled=trusting_all,
            ),
            MenuOption(
                value=_STARTUP_CONTINUE,
                label="Continue without trusting (hooks won't run)",
                disabled=trusting_all,
            ),
        ),
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
    )


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


async def _run_hook_action(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    entry: HookCatalogEntry,
    event: str,
    action: typing.Any,
    *,
    session_id: int | None
) -> None:
    """执行 Hook 变更并按稳定标识刷新仍在栈中的菜单。"""
    if (
        session_id is None
        or not runtime.menu_session_is_active(session_id)
    ):
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
        refreshed = _refresh_catalog(
            mind,
            workspace,
            catalog,
            runtime=runtime,
            session_id=session_id,
        )
        if runtime.menu_session_is_active(session_id):
            runtime.replace_present_menu_if_id(
                "hooks:events",
                _hook_root_request(runtime, mind, workspace, refreshed),
                session_id=session_id,
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
                session_id=session_id,
            )
    except ValueError as error:
        if runtime.menu_session_is_active(session_id):
            runtime.push_menu(hook_failure_panel(
                entry,
                error,
                on_ctrl_c=lambda: _close_hooks_browser(runtime),
            ))


async def _run_batch_trust(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    *,
    session_id: int | None
) -> None:
    """信任当前快照中所有待审核 Hook 并刷新事件页。"""
    if (
        session_id is None
        or not runtime.menu_session_is_active(session_id)
    ):
        return None

    pending = tuple(item for item in catalog.hooks if item.needs_review)

    try:
        mind.trust_hooks(
            tuple((entry.key, entry.content_hash) for entry in pending),
            workspace=workspace,
        )
    except ValueError as error:
        if runtime.menu_session_is_active(session_id):
            refreshed = _refresh_catalog(
                mind,
                workspace,
                catalog,
                runtime=runtime,
                session_id=session_id,
            )
            runtime.replace_present_menu_if_id(
                "hooks:events",
                _hook_root_request(runtime, mind, workspace, refreshed),
                session_id=session_id,
            )
            runtime.push_menu(hook_failure_panel(
                pending[0],
                error,
                on_ctrl_c=lambda: _close_hooks_browser(runtime),
            ))
        return None

    refreshed = _refresh_catalog(
        mind,
        workspace,
        catalog,
        runtime=runtime,
        session_id=session_id,
    )
    if runtime.menu_session_is_active(session_id):
        runtime.replace_present_menu_if_id(
            "hooks:events",
            _hook_root_request(runtime, mind, workspace, refreshed),
            session_id=session_id,
        )


async def manage_hooks(
    runtime: "TuiRuntime",
    mind: "Mind",
    *,
    catalog: HookCatalogSnapshot | None = None
) -> None:
    """在主 TUI 中查看 Hook 并管理显式信任。"""
    workspace = Path(mind.history_workspace)
    if catalog is None:
        try:
            catalog = mind.inspect_hooks(workspace=workspace)
        except (ConfigStoreError, ConfigValidationError) as error:
            render_hooks_failure(mind.frontend.application, error)
            return None

    await runtime.select_menu(
        _hook_root_request(runtime, mind, workspace, catalog)
    )


async def review_startup_hooks(
    runtime: "TuiRuntime",
    mind: "Mind"
) -> HookCatalogSnapshot | None:
    """在交互会话启动前选择是否打开 Hook 审核浏览器。"""
    owns_startup_gate = not runtime.startup_gate_active
    if owns_startup_gate:
        runtime.begin_startup_gate()

    try:
        workspace = Path(mind.history_workspace)
        try:
            catalog = mind.inspect_hooks(workspace=workspace)
        except (ConfigStoreError, ConfigValidationError):
            return None

        if not any(item.needs_review for item in catalog.hooks):
            return None

        selection = await runtime.select_menu(
            _startup_hooks_review_request(runtime, mind, workspace, catalog)
        )
        return catalog if selection == _STARTUP_REVIEW else None
    finally:
        if owns_startup_gate:
            await runtime.finish_startup_gate()


async def _run_startup_hook_trust(
    runtime: "TuiRuntime",
    mind: "Mind",
    workspace: Path,
    catalog: HookCatalogSnapshot,
    *,
    session_id: int
) -> None:
    """执行启动阶段的批量信任并保护菜单会话。"""

    try:
        if not runtime.menu_session_is_active(session_id):
            return None
        mind.trust_hooks(
            tuple(
                (entry.key, entry.content_hash)
                for entry in catalog.hooks
                if entry.needs_review
            ),
            workspace=workspace,
        )
    except ValueError as trust_error:
        if not runtime.menu_session_is_active(session_id):
            return None

        try:
            refreshed = mind.inspect_hooks(workspace=workspace)
        except (ConfigStoreError, ConfigValidationError):
            refreshed = catalog

        runtime.replace_active_menu_if_id(
            "hooks:startup-review",
            _startup_hooks_review_request(
                runtime,
                mind,
                workspace,
                refreshed,
                error=f"Failed to trust hooks: {trust_error}",
                trusting_all=False,
            ),
            session_id=session_id,
        )
        return None

    if runtime.menu_session_is_active(session_id):
        runtime.finish_menu(_STARTUP_CONTINUE)


if __name__ == '__main__':
    pass
