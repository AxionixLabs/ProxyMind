# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.terminal_text import (
    sanitize_terminal_line,
    sanitize_terminal_text
)
from ...contracts.menu import (
    MenuColumnWidthMode,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    MenuTab
)


def sanitize_menu_request(request: MenuRequest) -> MenuRequest:
    """复制菜单请求并清理其中的显示字段。"""
    return MenuRequest(
        title=sanitize_terminal_line(request.title),
        title_accent_suffix=sanitize_inline_text(request.title_accent_suffix),
        options=sanitize_menu_options(request.options),
        body=tuple(
            sanitize_inline_text(line)
            if request.body_preserve_spacing
            else sanitize_terminal_line(line)
            for line in request.body
        ),
        body_warning=sanitize_terminal_line(request.body_warning),
        selected=request.selected,
        status=sanitize_terminal_line(request.status),
        status_style=sanitize_terminal_line(request.status_style),
        help_text=sanitize_terminal_line(request.help_text),
        view_id=sanitize_terminal_line(request.view_id or "") or None,
        generation=max(0, int(request.generation)),
        searchable=request.searchable,
        search_placeholder=sanitize_terminal_line(request.search_placeholder),
        footer_note=sanitize_terminal_line(request.footer_note),
        footer_hint=sanitize_terminal_line(request.footer_hint),
        allow_cancel=request.allow_cancel,
        description_layout=sanitize_description_layout(request.description_layout),
        description_separator=sanitize_inline_text(request.description_separator),
        min_description_width=max(1, int(request.min_description_width)),
        tabs=tuple(sanitize_menu_tab(tab) for tab in request.tabs),
        active_tab_id=sanitize_terminal_line(request.active_tab_id or "") or None,
        column_width_mode=sanitize_column_width_mode(request.column_width_mode),
        name_column_width=(
            max(1, int(request.name_column_width))
            if request.name_column_width is not None
            else None
        ),
        table_column_widths=tuple(
            max(0, int(width)) for width in request.table_column_widths
        ),
        on_space=request.on_space,
        on_t=request.on_t,
        on_ctrl_c=request.on_ctrl_c,
        show_option_gutter=bool(request.show_option_gutter),
        show_all_options=bool(request.show_all_options),
        body_inset=bool(request.body_inset),
        body_as_table_header=bool(request.body_as_table_header),
        body_preserve_spacing=bool(request.body_preserve_spacing),
        body_styles=tuple(
            sanitize_terminal_line(style) for style in request.body_styles
        ),
        body_fragments=tuple(
            tuple(
                (sanitize_terminal_line(style), sanitize_inline_text(text))
                for style, text in line
            )
            for line in request.body_fragments
        ),
        body_wrap=bool(request.body_wrap),
        body_line_limits=tuple(
            max(1, int(limit)) if limit is not None else None
            for limit in request.body_line_limits
        ),
    )


def sanitize_description_layout(value: typing.Any) -> MenuDescriptionLayout:
    """清理菜单描述排列策略并回退到默认模式。"""
    try:
        return MenuDescriptionLayout(value)
    except (TypeError, ValueError):
        return MenuDescriptionLayout.COLUMNS


def sanitize_inline_text(value: typing.Any) -> str:
    """清理单行显示文本并保留其布局空白。"""
    raw = sanitize_terminal_text(value)
    if not raw:
        return ""
    return raw.replace("\n", " ")


def sanitize_column_width_mode(value: typing.Any) -> MenuColumnWidthMode:
    """清理菜单列宽模式并回退到稳定默认值。"""
    try:
        return MenuColumnWidthMode(value)
    except (TypeError, ValueError):
        return MenuColumnWidthMode.AUTO_ALL_ROWS


def sanitize_menu_tab(tab: MenuTab) -> MenuTab:
    """清理菜单页签的标识、标题、选项和提示文本。"""
    return MenuTab(
        tab_id=sanitize_terminal_line(tab.tab_id),
        label=sanitize_terminal_line(tab.label),
        options=sanitize_menu_options(tab.options),
        footer_hint=(
            sanitize_terminal_line(tab.footer_hint)
            if tab.footer_hint is not None
            else None
        ),
    )


def sanitize_menu_option(option: MenuOption) -> MenuOption:
    """清理菜单选项的显示文本并保留其交互状态。"""
    return MenuOption(
        value=option.value,
        label=sanitize_terminal_line(option.label),
        detail=sanitize_terminal_line(option.detail),
        on_select=option.on_select,
        dismiss_on_select=option.dismiss_on_select,
        dismiss_parent_on_child_accept=option.dismiss_parent_on_child_accept,
        disabled=option.disabled,
        disabled_reason=sanitize_terminal_line(option.disabled_reason),
        selected_detail=sanitize_terminal_line(option.selected_detail),
        is_current=option.is_current,
        is_default=option.is_default,
        search_value=(
            sanitize_terminal_line(option.search_value)
            if option.search_value is not None
            else None
        ),
        disabled_gutter_marker=sanitize_terminal_line(option.disabled_gutter_marker),
        selected_body=tuple(
            sanitize_inline_text(line) for line in option.selected_body
        ),
        selected_footer_hint=sanitize_terminal_line(option.selected_footer_hint),
        columns=tuple(sanitize_terminal_line(value) for value in option.columns),
        column_styles=tuple(
            sanitize_terminal_line(style) for style in option.column_styles
        ),
        row_style=sanitize_terminal_line(option.row_style),
        selected_row_style=sanitize_terminal_line(option.selected_row_style),
        selected_body_fragments=tuple(
            tuple(
                (sanitize_terminal_line(style), sanitize_inline_text(text))
                for style, text in line
            )
            for line in option.selected_body_fragments
        ),
        selected_body_line_limits=tuple(
            max(1, int(limit)) if limit is not None else None
            for limit in option.selected_body_line_limits
        ),
    )


def sanitize_menu_options(options: tuple[MenuOption, ...]) -> tuple[MenuOption, ...]:
    """清理菜单选项集合。"""
    return tuple(sanitize_menu_option(option) for option in options)


if __name__ == '__main__':
    pass
