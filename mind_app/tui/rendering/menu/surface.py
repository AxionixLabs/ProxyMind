# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import (
    dataclass,
    replace
)
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth
from ...contracts.menu import (
    MenuOption,
    MenuRequest
)
from ..fragments import (
    clip_fragments,
    clip_text
)
from .layout import (
    rows_width,
    surface_content_width,
    surface_inset_fragments
)
from .renderer import (
    body_fragments,
    header_fragments,
    join_surface_sections,
    tab_fragments,
    wrapped_text_fragments
)
from .rows import (
    enabled_number_width,
    option_fragments,
    option_prefix, row_layout
)
from .selection import (
    option_is_disabled,
    visible_window
)
from .state import MenuState


@dataclass(frozen=True, slots=True)
class MenuRenderConfig(object):
    """保存菜单渲染和测量共享的稳定尺寸约束。"""
    visible_rows: int
    horizontal_inset: int
    min_label_width: int
    min_detail_width: int
    max_detail_reserve: int


def surface_fragments(
    state: MenuState,
    *,
    width: int,
    config: MenuRenderConfig,
) -> StyleAndTextTuples:
    """生成指定宽度下的菜单表面内容。"""
    request = state.request
    content_width = surface_content_width(
        width,
        inset=config.horizontal_inset,
    )
    available_rows_width = rows_width(width)
    _start, visible_indices = visible_window(
        state,
        visible_rows=config.visible_rows,
    )
    options = tuple(request.options[index] for index in visible_indices)
    number_width = enabled_number_width(state)
    row_prefix_width, label_width = row_layout(
        request,
        width=available_rows_width,
        number_width=number_width,
        visible_indices=visible_indices,
        surface_inset=config.horizontal_inset,
        min_label_width=config.min_label_width,
        min_detail_width=config.min_detail_width,
        max_detail_reserve=config.max_detail_reserve,
    )

    header: StyleAndTextTuples = header_fragments(
        request,
        width=content_width,
    )
    header.append(("", "\n"))
    tabs = tab_fragments(request, width=content_width)
    if tabs:
        header.extend(tabs)
        header.append(("", "\n"))
    if request.help_text:
        header.extend([
            (
                "class:tui-menu.help",
                clip_text(request.help_text, width=content_width),
            ),
            ("", "\n"),
        ])
    header.extend(body_fragments(request, width=content_width))
    if request.searchable:
        query = state.query or request.search_placeholder
        query_style = (
            "class:tui-menu.search"
            if state.query
            else "class:tui-menu.search.placeholder"
        )
        header.extend([
            ("class:tui-menu.search", "  Search: "),
            (
                query_style,
                clip_text(query, width=max(1, content_width - 10)),
            ),
            ("", "\n"),
        ])

    rows_out: StyleAndTextTuples = []
    for offset, option in enumerate(options):
        index = visible_indices[offset]
        active = index == state.selected and not option_is_disabled(option)
        index_style = (
            "class:tui-menu.index.disabled"
            if option_is_disabled(option)
            else "class:tui-menu.index.active"
            if active
            else "class:tui-menu.index"
        )
        prefix = option_prefix(
            state,
            index,
            active=active,
            number_width=number_width,
            surface_inset=config.horizontal_inset,
        )
        rows = option_fragments(
            option,
            available=max(1, available_rows_width - get_cwidth(prefix)),
            label_width=label_width,
            active=active,
            request=request,
            row_prefix_width=row_prefix_width,
            width=available_rows_width,
            index_style=index_style,
            prefix=prefix,
        )
        for row in rows:
            rows_out.extend(clip_fragments(row, width=available_rows_width))
            rows_out.append(("", "\n"))

    selected = _selected_option(state)
    if selected is not None and (
        selected.selected_body or selected.selected_body_fragments
    ):
        rows_out.append(("", "\n"))
        rows_out.extend(body_fragments(
            replace(
                request,
                body=selected.selected_body,
                body_fragments=selected.selected_body_fragments,
                body_line_limits=selected.selected_body_line_limits,
            ),
            width=content_width,
        ))

    return join_surface_sections(
        surface_inset_fragments(
            header,
            inset=config.horizontal_inset,
        ),
        rows_out,
        separate=not request.body_as_table_header,
    )


def footer_fragments(
    state: MenuState,
    *,
    width: int,
) -> StyleAndTextTuples:
    """生成指定菜单状态的透明 footer 片段。"""
    return _request_footer_fragments(
        _request_with_selected_footer(state),
        width=max(1, int(width)),
    )


def surface_footer_fragments(
    state: MenuState,
    *,
    width: int,
    config: MenuRenderConfig,
) -> StyleAndTextTuples:
    """生成兼容独立菜单文本的 surface 内 footer。"""
    return _request_footer_fragments(
        _request_with_selected_footer(state),
        width=surface_content_width(
            width,
            inset=config.horizontal_inset,
        ),
    )


def menu_fragments(
    state: MenuState,
    *,
    width: int,
    config: MenuRenderConfig,
) -> StyleAndTextTuples:
    """生成包含独立 footer 的完整菜单片段。"""
    surface = surface_fragments(state, width=width, config=config)
    footer = surface_footer_fragments(state, width=width, config=config)
    if not footer:
        return surface
    surface.append(("", "\n"))
    surface.extend([
        ("class:tui-menu.surface", "  "),
        ("", "\n"),
    ])
    surface.extend(surface_inset_fragments(
        footer,
        inset=config.horizontal_inset,
    ))
    return surface


def _request_with_selected_footer(state: MenuState) -> MenuRequest:
    """返回应用选中项 footer 覆盖后的请求。"""
    request = state.request
    selected = _selected_option(state)
    if selected is None or not selected.selected_footer_hint:
        return request
    return replace(request, footer_hint=selected.selected_footer_hint)


def _request_footer_fragments(
    request: MenuRequest,
    *,
    width: int,
) -> StyleAndTextTuples:
    """生成可选 footer note 和 hint 的包裹片段。"""
    out: StyleAndTextTuples = []
    inner_width = max(1, width - 2)
    if request.footer_note:
        out.extend(wrapped_text_fragments(
            request.footer_note,
            style="class:tui-menu.footer.note",
            width=inner_width,
        ))
    if request.footer_hint and request.allow_cancel:
        out.extend(wrapped_text_fragments(
            request.footer_hint,
            style="class:tui-menu.footer.hint",
            width=inner_width,
        ))
    return out


def _selected_option(state: MenuState) -> MenuOption | None:
    """返回当前菜单选中项。"""
    if 0 <= state.selected < len(state.request.options):
        return state.request.options[state.selected]
    return None


if __name__ == '__main__':
    pass
