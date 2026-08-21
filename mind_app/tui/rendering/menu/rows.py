# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth
from ...contracts.menu import (
    MenuColumnWidthMode,
    MenuOption,
    MenuRequest
)
from ..fragments import (
    clip_text,
    wrap_formatted_lines
)
from .layout import (
    prefix_width,
    search_prefix_width,
    should_stack_description,
)
from .selection import (
    filtered_indices,
    option_detail,
    option_is_disabled,
)
from .state import MenuState
from .renderer import strip_leading_spaces


def option_fragments(
    option: MenuOption,
    *,
    available: int,
    label_width: int | None,
    active: bool,
    request: MenuRequest,
    row_prefix_width: int,
    width: int,
    index_style: str,
    prefix: str,
) -> list[StyleAndTextTuples]:
    """按可用宽度分配选项主标签和辅助信息。"""
    if option_is_disabled(option):
        label_style = "class:tui-menu.label.disabled"
        detail_style = "class:tui-menu.detail.disabled"
    else:
        default_label_style = (
            "class:tui-menu.label.active" if active else "class:tui-menu.label"
        )
        label_style = (
            option.selected_row_style
            if active and option.selected_row_style
            else option.row_style
            if not active and option.row_style
            else default_label_style
        )
        detail_style = (
            "class:tui-menu.detail-selected" if active else "class:tui-menu.detail"
        )

    if option.columns and request.table_column_widths:
        widths = list(request.table_column_widths)
        values = list(option.columns)
        if len(values) < len(widths):
            values.extend([""] * (len(widths) - len(values)))
        values = values[:len(widths)]
        separator = request.description_separator
        gap_width = get_cwidth(separator) * max(0, len(widths) - 1)
        fixed_width = sum(widths[:-1])
        if widths[-1] <= 0:
            widths[-1] = max(1, available - fixed_width - gap_width)
        total_width = sum(widths) + gap_width
        if total_width > available:
            widths[-1] = max(1, widths[-1] - (total_width - available))

        cells: list[tuple[str, str]] = []
        for index, (value, cell_width) in enumerate(zip(values, widths)):
            cell = clip_text(value, width=cell_width)
            if index < len(widths) - 1:
                cell += " " * max(0, cell_width - get_cwidth(cell))
            cell_style = label_style
            if not active and index < len(option.column_styles):
                cell_style = option.column_styles[index] or label_style
            cells.append((cell_style, cell))

        row: StyleAndTextTuples = [(index_style, prefix)]
        for index, (cell_style, cell) in enumerate(cells):
            if index:
                row.append((label_style, separator))
            row.append((cell_style, cell))
        return [row]

    detail = option_detail(option, active=active)
    if detail and should_stack_description(
        request,
        detail=detail,
        available=available,
        label_width=label_width,
    ):
        label = clip_text(option.label, width=available)
        rows: list[StyleAndTextTuples] = [[
            (index_style, prefix),
            (label_style, label),
        ]]
        detail_width = max(1, width - row_prefix_width)
        for detail_row in wrap_formatted_lines(
            [(detail_style, detail)],
            width=detail_width,
        ):
            rows.append([
                (detail_style, " " * row_prefix_width),
                *strip_leading_spaces(detail_row),
            ])
        return rows

    if not detail or label_width is None:
        return [[
            (index_style, prefix),
            (label_style, clip_text(option.label, width=available)),
        ]]

    separator = request.description_separator
    separator_width = get_cwidth(separator)
    label = clip_text(option.label, width=label_width)
    padding = " " * max(0, label_width - get_cwidth(label))
    detail_width = available - label_width - separator_width
    return [[
        (index_style, prefix),
        (label_style, f"{label}{padding}"),
        (detail_style, f"{separator}{clip_text(detail, width=detail_width)}"),
    ]]


def row_layout(
    request: MenuRequest,
    *,
    width: int,
    number_width: int,
    visible_indices: tuple[int, ...],
    surface_inset: int,
    min_label_width: int,
    min_detail_width: int,
    max_detail_reserve: int,
) -> tuple[int, int | None]:
    """计算当前窗口的选项前缀和共享标签列宽。"""
    prefix_size = (
        surface_inset * 2
        if not request.show_option_gutter
        else (
            search_prefix_width()
            if request.searchable
            else prefix_width(number_width)
        )
    )
    prefix_size = max(0, prefix_size - surface_inset)
    label_size = label_column_width(
        request,
        available=max(1, width - prefix_size),
        visible_indices=visible_indices,
        min_label_width=min_label_width,
        min_detail_width=min_detail_width,
        max_detail_reserve=max_detail_reserve,
    )
    return prefix_size, label_size


def enabled_number_width(state: MenuState) -> int:
    """返回当前过滤结果中可执行候选的序号宽度。"""
    count = sum(
        not option_is_disabled(state.request.options[index])
        for index in filtered_indices(state)
    )
    return len(str(max(1, count)))


def option_prefix(
    state: MenuState,
    index: int,
    *,
    active: bool,
    number_width: int,
    surface_inset: int,
) -> str:
    """生成候选项的选择标记和可执行序号 gutter。"""
    if not state.request.show_option_gutter:
        return " " * surface_inset
    marker = "›" if active else " "
    if state.request.searchable:
        return f"{marker} "
    option = state.request.options[index]
    if option_is_disabled(option):
        gutter_marker = clip_text(option.disabled_gutter_marker, width=number_width)
        gutter = (
            f"{gutter_marker.rjust(number_width)}  "
            if gutter_marker
            else " " * (number_width + 2)
        )
        return f"{marker} {gutter}"
    enabled_indices = tuple(
        candidate
        for candidate in filtered_indices(state)
        if not option_is_disabled(state.request.options[candidate])
    )
    number = enabled_indices.index(index) + 1
    return f"{marker} {str(number).rjust(number_width)}. "


def label_column_width(
    request: MenuRequest,
    *,
    available: int,
    visible_indices: tuple[int, ...],
    min_label_width: int,
    min_detail_width: int,
    max_detail_reserve: int,
) -> int | None:
    """计算全部选项共用的主标签列宽。"""
    options = request.options
    measurement_options = (
        tuple(options[index] for index in visible_indices)
        if request.column_width_mode is MenuColumnWidthMode.AUTO_VISIBLE
        else options
    )
    natural_label_width = max(
        (get_cwidth(option.label) for option in measurement_options),
        default=0,
    )
    detail_width = max(
        (
            get_cwidth(option_detail(option, active=True))
            for option in measurement_options
            if option_detail(option, active=True)
        ),
        default=0,
    )
    if detail_width <= 0:
        return None

    detail_reserve = min(
        detail_width,
        max(min_detail_width, min(max_detail_reserve, available // 3)),
    )
    max_label_width = (
        available
        - get_cwidth(request.description_separator)
        - detail_reserve
    )
    if max_label_width < min_label_width:
        return None
    if request.column_width_mode is MenuColumnWidthMode.FIXED:
        return max(min_label_width, min(max_label_width, available * 3 // 10))
    requested_width = request.name_column_width or 0
    return min(max(natural_label_width, requested_width), max_label_width)
