# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from frontends.terminal.text import (
    sanitize_terminal_line,
    sanitize_terminal_text
)
from frontends.tui.contracts.menu import (
    MenuColumnWidthMode,
    MenuDescriptionLayout,
    MenuEmptyAcceptAction,
    MenuFooterCommand,
    MenuFooterHint,
    MenuFooterTone,
    MenuFooterValue,
    MenuOption,
    MenuRequest,
    MenuRowDisplay,
    MenuTab,
    MenuTextInputMode,
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
        text_input_mode=sanitize_text_input_mode(request.text_input_mode),
        text_input_max_rows=max(1, int(request.text_input_max_rows)),
        text_input_result_factory=request.text_input_result_factory,
        initial_query=(
            sanitize_terminal_text(request.initial_query)
            if request.text_input_mode is MenuTextInputMode.MULTILINE
            else sanitize_terminal_line(request.initial_query)
        ),
        text_input_gutter=sanitize_terminal_line(request.text_input_gutter),
        text_input_gutter_style=sanitize_terminal_line(
            request.text_input_gutter_style
        ),
        search_placeholder=sanitize_terminal_line(request.search_placeholder),
        search_matcher=request.search_matcher,
        search_ranker=request.search_ranker,
        search_prompt_prefix=sanitize_inline_text(request.search_prompt_prefix),
        search_prompt_style=sanitize_terminal_line(request.search_prompt_style),
        search_help_text=sanitize_terminal_line(request.search_help_text),
        search_query_style=sanitize_terminal_line(request.search_query_style),
        search_empty_text=sanitize_terminal_line(request.search_empty_text),
        empty_accept_action=sanitize_empty_accept_action(
            request.empty_accept_action
        ),
        footer_note=sanitize_terminal_line(request.footer_note),
        footer_hint=sanitize_footer_hint(request.footer_hint),
        footer_tone=sanitize_footer_tone(request.footer_tone),
        footer_right=sanitize_inline_text(request.footer_right),
        footer_right_active=sanitize_inline_text(request.footer_right_active),
        allow_cancel=request.allow_cancel,
        description_layout=sanitize_description_layout(request.description_layout),
        description_separator=sanitize_inline_text(request.description_separator),
        min_description_width=max(1, int(request.min_description_width)),
        tabs=tuple(sanitize_menu_tab(tab) for tab in request.tabs),
        active_tab_id=sanitize_terminal_line(request.active_tab_id or "") or None,
        tabs_in_header=bool(request.tabs_in_header),
        surface_style=sanitize_inline_text(request.surface_style),
        column_width_mode=sanitize_column_width_mode(request.column_width_mode),
        row_display=sanitize_row_display(request.row_display),
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
        body_as_table_header=bool(request.body_as_table_header),
        separate_options=bool(request.separate_options),
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
        selection_marker=sanitize_inline_text(request.selection_marker) or "›",
        surface_horizontal_inset=(
            max(0, int(request.surface_horizontal_inset))
            if request.surface_horizontal_inset is not None
            else None
        ),
    )


def sanitize_description_layout(value: typing.Any) -> MenuDescriptionLayout:
    """清理菜单描述排列策略并回退到默认模式。"""
    try:
        return MenuDescriptionLayout(value)
    except (TypeError, ValueError):
        return MenuDescriptionLayout.COLUMNS


def sanitize_empty_accept_action(value: typing.Any) -> MenuEmptyAcceptAction:
    """清理无结果确认行为并回退到关闭菜单。"""
    try:
        return MenuEmptyAcceptAction(value)
    except (TypeError, ValueError):
        return MenuEmptyAcceptAction.CANCEL


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


def sanitize_row_display(value: typing.Any) -> MenuRowDisplay:
    """清理候选行展示策略并回退到截断模式。"""
    try:
        return MenuRowDisplay(value)
    except (TypeError, ValueError):
        return MenuRowDisplay.CLIPPED


def sanitize_footer_tone(value: typing.Any) -> MenuFooterTone:
    """清理菜单页脚语义层级并回退到默认层级。"""
    try:
        return MenuFooterTone(value)
    except (TypeError, ValueError):
        return MenuFooterTone.DEFAULT


def sanitize_text_input_mode(value: typing.Any) -> MenuTextInputMode:
    """清理菜单文本输入模式并回退到无编辑状态。"""
    try:
        return MenuTextInputMode(value)
    except (TypeError, ValueError):
        return MenuTextInputMode.NONE


def sanitize_menu_tab(tab: MenuTab) -> MenuTab:
    """清理菜单页签的标识、标题、选项和提示文本。"""
    return MenuTab(
        tab_id=sanitize_terminal_line(tab.tab_id),
        label=sanitize_terminal_line(tab.label),
        options=sanitize_menu_options(tab.options),
        footer_hint=(
            sanitize_footer_hint(tab.footer_hint)
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
        selected_footer_hint=sanitize_footer_hint(option.selected_footer_hint),
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
        category=sanitize_terminal_line(option.category),
        category_style=sanitize_terminal_line(option.category_style),
    )


def sanitize_menu_options(options: tuple[MenuOption, ...]) -> tuple[MenuOption, ...]:
    """清理菜单选项集合。"""
    return tuple(sanitize_menu_option(option) for option in options)


def sanitize_footer_hint(value: MenuFooterValue) -> MenuFooterValue:
    """清理静态 footer 或结构化快捷键 footer。"""
    if not isinstance(value, MenuFooterHint):
        return sanitize_terminal_line(value)
    return MenuFooterHint(
        commands=tuple(
            MenuFooterCommand(
                actions=command.actions,
                description=sanitize_terminal_line(command.description),
                key_labels=tuple(
                    sanitize_terminal_line(label)
                    for label in command.key_labels
                ),
            )
            for command in value.commands
        ),
        prefix=sanitize_inline_text(value.prefix),
        separator=sanitize_inline_text(value.separator),
    )


if __name__ == '__main__':
    pass
