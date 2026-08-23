# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from enum import Enum
from .text import FormattedLine


STANDARD_MENU_FOOTER_HINT: typing.Final[str] = (
    "Press enter to confirm or esc to go back"
)

CLOSE_MENU_FOOTER_HINT: typing.Final[str] = "Press enter or esc to close"


class MenuDescriptionLayout(str, Enum):
    """描述菜单选项辅助文本的排列方式。"""
    COLUMNS = "columns"
    STACK_BELOW_WHEN_NARROW = "stack_below_when_narrow"


class MenuColumnWidthMode(str, Enum):
    """描述菜单标签列宽的计算范围。"""
    AUTO_VISIBLE = "auto_visible"
    AUTO_ALL_ROWS = "auto_all_rows"
    FIXED = "fixed"


class MenuActionKind(str, Enum):
    """区分菜单导航事件和领域操作事件。"""
    NAVIGATION = "navigation"
    DOMAIN = "domain"


class MenuEmptyAcceptAction(str, Enum):
    """描述菜单没有可选结果时确认键的行为。"""
    CANCEL = "cancel"
    IGNORE = "ignore"


@dataclass(frozen=True, slots=True)
class MenuOption(object):
    """描述运行期选择菜单中的一项。"""
    value: typing.Any
    label: str
    detail: str = ""
    on_select: typing.Callable[[], None] | None = None
    dismiss_on_select: bool = True
    dismiss_parent_on_child_accept: bool = False
    disabled: bool = False
    disabled_reason: str = ""
    selected_detail: str = ""
    is_current: bool = False
    is_default: bool = False
    search_value: str | None = None
    disabled_gutter_marker: str = ""
    selected_body: tuple[str, ...] = ()
    selected_footer_hint: str = ""
    columns: tuple[str, ...] = ()
    column_styles: tuple[str, ...] = ()
    row_style: str = ""
    selected_row_style: str = ""
    selected_body_fragments: tuple[FormattedLine, ...] = ()
    selected_body_line_limits: tuple[int | None, ...] = ()
    category: str = ""
    category_style: str = ""


@dataclass(frozen=True, slots=True)
class MenuTab(object):
    """描述选择菜单中的一个分类页签。"""
    tab_id: str
    label: str
    options: tuple[MenuOption, ...] = ()
    footer_hint: str | None = None


@dataclass(frozen=True, slots=True)
class MenuAction(object):
    """描述一次需要在菜单事件边界执行的动作。"""
    callback: typing.Callable[[], None]
    name: str = "tui menu action"
    session_id: int | None = None
    kind: MenuActionKind = MenuActionKind.DOMAIN


@dataclass(frozen=True, slots=True)
class MenuRequest(object):
    """描述运行期内嵌选择菜单及其生命周期回调。"""
    title: str
    options: tuple[MenuOption, ...] = ()
    body: tuple[str, ...] = ()
    selected: int = 0
    status: str = ""
    help_text: str = ""
    view_id: str | None = None
    generation: int = 0
    searchable: bool = False
    search_placeholder: str = "Search"
    search_matcher: typing.Callable[[str, MenuOption], bool] | None = None
    search_ranker: typing.Callable[
        [str, MenuOption],
        tuple[int, str] | None,
    ] | None = None
    search_prompt_prefix: str = "  Search: "
    search_prompt_style: str = ""
    search_help_text: str = ""
    search_query_style: str = ""
    search_empty_text: str = ""
    empty_accept_action: MenuEmptyAcceptAction = MenuEmptyAcceptAction.CANCEL
    footer_note: str = ""
    footer_hint: str = ""
    footer_right: str = ""
    footer_right_active: str = ""
    allow_cancel: bool = True
    description_layout: MenuDescriptionLayout = MenuDescriptionLayout.COLUMNS
    description_separator: str = "  "
    min_description_width: int = 24
    tabs: tuple[MenuTab, ...] = ()
    active_tab_id: str | None = None
    tabs_in_header: bool = True
    surface_style: str = "class:menu-card"
    column_width_mode: MenuColumnWidthMode = MenuColumnWidthMode.AUTO_ALL_ROWS
    name_column_width: int | None = None
    title_accent_suffix: str = ""
    body_warning: str = ""
    on_space: typing.Callable[[], None] | None = None
    on_t: typing.Callable[[], None] | None = None
    on_ctrl_c: typing.Callable[[], bool] | None = None
    table_column_widths: tuple[int, ...] = ()
    show_option_gutter: bool = True
    show_all_options: bool = False
    body_as_table_header: bool = False
    separate_options: bool = True
    body_preserve_spacing: bool = False
    body_styles: tuple[str, ...] = ()
    body_fragments: tuple[FormattedLine, ...] = ()
    body_wrap: bool = False
    body_line_limits: tuple[int | None, ...] = ()
    status_style: str = ""
    selection_marker: str = "›"


if __name__ == '__main__':
    pass
