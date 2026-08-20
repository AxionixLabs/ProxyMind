# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from enum import Enum
from dataclasses import (
    dataclass,
    field
)
from pathlib import Path

FormattedText: typing.TypeAlias = list[tuple[str, str]]
FormattedLine: typing.TypeAlias = tuple[tuple[str, str], ...]

TranscriptExportFormat: typing.TypeAlias = typing.Literal[
    "markdown",
    "raw"
]

STANDARD_MENU_FOOTER_HINT: typing.Final[str] = (
    "Press enter to confirm or esc to go back"
)

CLOSE_MENU_FOOTER_HINT: typing.Final[str] = "Press enter or esc to close"


class ViewCompletion(str, Enum):
    """描述交互视图的终止语义。"""
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"


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


class TranscriptExportResult(typing.Protocol):
    """描述记录导出回调返回的结构化结果。"""
    path: Path
    format: TranscriptExportFormat
    cell_count: int


@dataclass(frozen=True, slots=True)
class LineFill(object):
    """描述单行内容随终端宽度延伸的填充规则。"""
    character: str
    margin: int = 0


@dataclass(frozen=True, slots=True)
class FragmentBlock(object):
    """保存无需再次转换的 prompt_toolkit 文本片段。"""
    fragments: tuple[tuple[str, str], ...]
    line_fill: LineFill | None = None


@dataclass(frozen=True, slots=True)
class TranscriptBacktrackRequest(object):
    """描述从完整记录中重新编辑一条用户输入的请求。"""
    turn_id: str
    prompt: str
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)


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
    """描述运行期内嵌选择菜单。"""
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
    footer_note: str = ""
    footer_hint: str = ""
    allow_cancel: bool = True
    description_layout: MenuDescriptionLayout = MenuDescriptionLayout.COLUMNS
    description_separator: str = "  "
    min_description_width: int = 24
    tabs: tuple[MenuTab, ...] = ()
    active_tab_id: str | None = None
    column_width_mode: MenuColumnWidthMode = MenuColumnWidthMode.AUTO_ALL_ROWS
    name_column_width: int | None = None
    title_accent_suffix: str = ""
    body_warning: str = ""
    on_space: typing.Callable[[], None] | None = None
    on_t: typing.Callable[[], None] | None = None
    table_column_widths: tuple[int, ...] = ()
    show_option_gutter: bool = True
    show_all_options: bool = False
    body_inset: bool = True
    body_as_table_header: bool = False
    body_preserve_spacing: bool = False
    body_styles: tuple[str, ...] = ()
    body_fragments: tuple[FormattedLine, ...] = ()
    status_style: str = ""


@dataclass(frozen=True, slots=True)
class MailboxEntry(object):
    """描述全屏收件箱中的一条只读消息。"""
    key: str
    title: str
    message: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MailboxRunRequest(object):
    """描述需要由 TUI 主循环串行执行的收件箱消息。"""
    message_id: str
    automatic: bool = False


if __name__ == '__main__':
    pass
