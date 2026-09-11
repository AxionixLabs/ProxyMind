# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import (
    dataclass,
    replace
)

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth

from frontends.tui.contracts.menu import (
    MenuFooterHint,
    MenuFooterTone,
    MenuFooterValue,
    MenuOption,
    MenuRequest,
    MenuTextInputMode,
)
from .layout import (
    rows_width,
    surface_content_width,
    surface_inset_fragments
)
from .measure import line_count
from .renderer import (
    body_fragments,
    header_fragments,
    join_surface_sections,
    strip_leading_spaces,
    tab_fragments,
    wrapped_text_fragments
)
from .rows import (
    enabled_number_width,
    option_fragments,
    option_prefix, row_layout
)
from .selection import (
    filtered_indices,
    option_is_disabled,
    visible_window
)
from .state import MenuState
from ..fragments import (
    clip_fragments,
    clip_text,
    fragments_text,
    iter_text_unit_ranges,
    wrap_formatted_lines,
)


@dataclass(frozen=True, slots=True)
class MenuRenderConfig(object):
    """保存菜单渲染和测量共享的稳定尺寸约束。"""
    visible_rows: int
    horizontal_inset: int
    min_label_width: int
    min_detail_width: int
    max_detail_reserve: int


def _option_row_groups(
    state: MenuState,
    *,
    indices: tuple[int, ...],
    request: MenuRequest,
    available_rows_width: int,
    surface_inset: int,
    config: MenuRenderConfig,
) -> tuple[tuple[int, tuple[StyleAndTextTuples, ...]], ...]:
    """生成一段候选窗口，并保留每个选项对应的物理行。"""
    number_width = enabled_number_width(state)
    row_prefix_width, label_width = row_layout(
        request,
        width=available_rows_width,
        number_width=number_width,
        visible_indices=indices,
        surface_inset=surface_inset,
        min_label_width=config.min_label_width,
        min_detail_width=config.min_detail_width,
        max_detail_reserve=config.max_detail_reserve,
    )
    groups: list[tuple[int, tuple[StyleAndTextTuples, ...]]] = []
    for index in indices:
        option = request.options[index]
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
            surface_inset=surface_inset,
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
        groups.append((index, tuple(rows)))
    return tuple(groups)


def _selected_visible_in_groups(
    groups: tuple[tuple[int, tuple[StyleAndTextTuples, ...]], ...],
    *,
    selected: int,
    row_budget: int,
) -> bool:
    """判断选择项的首行是否位于当前物理行预算内。"""
    if row_budget <= 0:
        return False
    used_rows = 0
    for index, rows in groups:
        row_height = max(1, len(rows))
        if used_rows > 0 and used_rows + row_height > row_budget:
            break
        if index == selected:
            return True
        used_rows += row_height
        if used_rows >= row_budget:
            break
    return False


def _option_row_budget(
    header: StyleAndTextTuples,
    *,
    max_height: int | None,
    separate: bool,
) -> int | None:
    """扣除头部和分隔行后返回候选区域的物理行预算。"""
    if max_height is None:
        return None
    probe = join_surface_sections(
        header,
        [("", "x"), ("", "\n")],
        separate=separate,
    )
    row_origin = max(0, line_count(probe) - 1)
    return max(0, int(max_height) - row_origin)


def _surface_inset(request: MenuRequest, config: MenuRenderConfig) -> int:
    """返回当前表面显式覆盖或共享默认的水平内缩。"""
    if request.surface_horizontal_inset is None:
        return config.horizontal_inset
    return max(0, int(request.surface_horizontal_inset))


def _text_input_fragments(
    query: str,
    cursor: int,
    *,
    style: str,
    width: int,
) -> StyleAndTextTuples:
    """生成光标始终位于可见窗口内的单行输入片段。"""
    limit = max(1, int(width))
    cursor = max(0, min(int(cursor), len(query)))
    start = 0
    for _unit_start, unit_end, _unit in iter_text_unit_ranges(query[:cursor]):
        if get_cwidth(query[start:cursor]) < limit:
            break
        start = unit_end

    end = cursor
    for _unit_start, unit_end, _unit in iter_text_unit_ranges(query[cursor:]):
        candidate_end = cursor + unit_end
        if get_cwidth(query[start:candidate_end]) > limit:
            break
        end = candidate_end

    trailing = query[cursor:end]
    if not trailing:
        trailing = " "
    return [
        (style, query[start:cursor]),
        ("[SetCursorPosition]", ""),
        (style, trailing),
    ]


def _multiline_input_rows(
    query: str,
    *,
    width: int,
) -> tuple[tuple[int, int, str], ...]:
    """按终端单元格宽度把多行输入拆成稳定可定位的视觉行。"""
    limit = max(1, int(width))
    rows: list[tuple[int, int, str]] = []
    line_start = 0
    logical_lines = query.split("\n")
    for line_index, line in enumerate(logical_lines):
        if not line:
            rows.append((line_start, line_start, ""))
        else:
            segment_start = 0
            segment_end = 0
            segment_width = 0
            for unit_start, unit_end, unit in iter_text_unit_ranges(line):
                unit_width = get_cwidth(unit)
                if segment_end > segment_start and segment_width + unit_width > limit:
                    rows.append((
                        line_start + segment_start,
                        line_start + segment_end,
                        line[segment_start:segment_end],
                    ))
                    segment_start = unit_start
                    segment_width = 0
                segment_end = unit_end
                segment_width += unit_width
            rows.append((
                line_start + segment_start,
                line_start + segment_end,
                line[segment_start:segment_end],
            ))
        line_start += len(line)
        if line_index < len(logical_lines) - 1:
            line_start += 1
    return tuple(rows)


def _multiline_text_input_fragments(
    state: MenuState,
    *,
    gutter: str,
    gutter_style: str,
    query_style: str,
    placeholder_style: str,
    width: int,
) -> StyleAndTextTuples:
    """生成保持光标可见且每行带 gutter 的多行编辑表面。"""
    request = state.request
    available = max(1, int(width) - get_cwidth(gutter))
    if not state.query:
        return [
            (gutter_style, gutter),
            ("[SetCursorPosition]", ""),
            (placeholder_style, request.search_placeholder or " "),
            ("", "\n"),
        ]

    rows = _multiline_input_rows(state.query, width=available)
    cursor = max(0, min(state.query_cursor, len(state.query)))
    cursor_rows = tuple(
        index
        for index, (start, end, _text) in enumerate(rows)
        if start <= cursor <= end
    )
    cursor_row = cursor_rows[-1] if cursor_rows else len(rows) - 1
    max_rows = max(1, request.text_input_max_rows)
    window_start = max(0, cursor_row - max_rows + 1)
    visible = rows[window_start:window_start + max_rows]

    out: StyleAndTextTuples = []
    for offset, (start, end, text) in enumerate(visible):
        row_index = window_start + offset
        out.append((gutter_style, gutter))
        if row_index == cursor_row:
            local_cursor = max(start, min(cursor, end))
            trailing = state.query[local_cursor:end] or " "
            out.extend((
                (query_style, state.query[start:local_cursor]),
                ("[SetCursorPosition]", ""),
                (query_style, trailing),
            ))
        else:
            out.append((query_style, text or " "))
        out.append(("", "\n"))
    return out


def surface_fragments(
    state: MenuState,
    *,
    width: int,
    config: MenuRenderConfig,
    max_height: int | None = None,
) -> StyleAndTextTuples:
    """生成指定宽度和可选物理高度下的菜单表面内容。"""
    request = state.request
    surface_inset = _surface_inset(request, config)
    content_width = surface_content_width(
        width,
        inset=surface_inset,
    )
    available_rows_width = rows_width(
        width,
        inset=surface_inset,
    )
    header: StyleAndTextTuples = (
        header_fragments(request, width=content_width)
        if request.title or request.status
        else []
    )
    if header:
        header.append(("", "\n"))
    tabs = (
        tab_fragments(request, width=content_width)
        if request.tabs_in_header
        else []
    )
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
    text_input = request.text_input_mode is not MenuTextInputMode.NONE
    if text_input and request.text_input_gutter:
        gutter_style = (
            request.text_input_gutter_style
            or "class:tui-menu.input-gutter"
        )
        header.extend([
            (gutter_style, request.text_input_gutter.rstrip()),
            ("", "\n"),
        ])
    if request.searchable or text_input:
        if request.search_help_text:
            if header:
                header.append(("", "\n"))
            header.extend([
                (
                    "class:tui-menu.search.placeholder",
                    clip_text(request.search_help_text, width=content_width),
                ),
                ("", "\n"),
            ])
        elif request.searchable and header:
            header.append(("", "\n"))
        query = state.query or request.search_placeholder

        query_style = (
            request.search_query_style or "class:tui-menu.search"
            if state.query
            else "class:tui-menu.search.placeholder"
        )

        prompt_prefix = (
            f"{request.text_input_gutter} "
            if text_input and request.text_input_gutter
            else request.search_prompt_prefix
        )
        if text_input and request.text_input_gutter:
            prompt_style = (
                request.text_input_gutter_style
                or "class:tui-menu.input-gutter"
            )
        else:
            prompt_style = request.search_prompt_style or query_style

        available_query_width = max(
            1,
            content_width - get_cwidth(prompt_prefix),
        )
        if request.text_input_mode is MenuTextInputMode.MULTILINE:
            header.extend(_multiline_text_input_fragments(
                state,
                gutter=prompt_prefix,
                gutter_style=prompt_style,
                query_style=(
                    request.search_query_style or "class:tui-menu.search"
                ),
                placeholder_style="class:tui-menu.search.placeholder",
                width=content_width,
            ))
            query_fragments = []
        elif request.text_input_mode is MenuTextInputMode.SINGLE_LINE:
            query_fragments = _text_input_fragments(
                state.query,
                state.query_cursor,
                style=query_style,
                width=available_query_width,
            )
        else:
            query_fragments = clip_fragments(
                [(query_style, query)],
                width=available_query_width,
            )

        if request.text_input_mode is not MenuTextInputMode.MULTILINE:
            header.extend([
                (prompt_style, prompt_prefix),
                *query_fragments,
                ("", "\n"),
            ])

    separate = (
        not request.body_as_table_header
        and request.separate_options
        and not (request.searchable or text_input)
    )
    inset_header = surface_inset_fragments(header, inset=surface_inset)
    row_budget = _option_row_budget(
        inset_header,
        max_height=max_height,
        separate=separate,
    )
    indices = filtered_indices(state)
    max_items = (
        len(indices)
        if request.show_all_options
        else max(1, int(config.visible_rows))
    )
    start, _visible_indices = visible_window(
        state,
        visible_rows=max_items,
    )
    selected_position = (
        indices.index(state.selected)
        if state.selected in indices
        else None
    )

    while True:
        visible_indices = indices[start:start + max_items]
        groups = _option_row_groups(
            state,
            indices=visible_indices,
            request=request,
            available_rows_width=available_rows_width,
            surface_inset=surface_inset,
            config=config,
        )
        if (
            row_budget is None
            or selected_position is None
            or start >= selected_position
            or _selected_visible_in_groups(
                groups,
                selected=state.selected,
                row_budget=row_budget,
            )
        ):
            break
        start += 1

    rows_out: StyleAndTextTuples = []
    used_rows = 0
    for _index, rows in groups:
        remaining = (
            len(rows)
            if row_budget is None
            else max(0, row_budget - used_rows)
        )
        if remaining <= 0:
            break
        visible_rows = rows[:remaining]
        for row in visible_rows:
            rows_out.extend(clip_fragments(row, width=available_rows_width))
            rows_out.append(("", "\n"))
        used_rows += len(visible_rows)
        if len(visible_rows) < len(rows):
            break

    if (
        request.searchable
        and request.search_empty_text
        and state.query
        and not indices
        and (row_budget is None or row_budget > 0)
    ):
        rows_out.extend([
            (
                "class:tui-menu.search.empty",
                f"{' ' * config.horizontal_inset}{request.search_empty_text}",
            ),
            ("", "\n"),
        ])

    selected = _selected_option(state)
    if selected is not None and (
        selected.selected_body or selected.selected_body_fragments
    ):
        rows_out.append(("", "\n"))
        rows_out.extend(surface_inset_fragments(
            body_fragments(
                replace(
                    request,
                    body=selected.selected_body,
                    body_fragments=selected.selected_body_fragments,
                    body_line_limits=selected.selected_body_line_limits,
                ),
                width=content_width,
            ),
            inset=surface_inset,
        ))

    return join_surface_sections(
        inset_header,
        rows_out,
        separate=separate,
    )


def footer_fragments(
    state: MenuState,
    *,
    width: int,
    inset: int = 0
) -> StyleAndTextTuples:
    """生成指定菜单状态的透明 footer 片段。"""
    return _inset_footer_fragments(
        _request_with_selected_footer(state),
        width=max(1, int(width)),
        inset=inset,
    )


def surface_footer_fragments(
    state: MenuState,
    *,
    width: int,
    config: MenuRenderConfig
) -> StyleAndTextTuples:
    """生成兼容独立菜单文本的 surface 内 footer。"""
    request = _request_with_selected_footer(state)
    surface_inset = _surface_inset(request, config)
    return _request_footer_fragments(
        request,
        width=max(1, int(width) - surface_inset),
    )


def menu_fragments(
    state: MenuState,
    *,
    width: int,
    config: MenuRenderConfig
) -> StyleAndTextTuples:
    """生成包含独立 footer 的完整菜单片段。"""
    surface = surface_fragments(state, width=width, config=config)
    footer = surface_footer_fragments(state, width=width, config=config)

    if not footer:
        return surface

    surface.append(("", "\n"))
    surface.extend([
        (
            "class:tui-menu.surface",
            " " * _surface_inset(state.request, config),
        ),
        ("", "\n"),
    ])
    surface.extend(surface_inset_fragments(
        footer,
        inset=_surface_inset(state.request, config),
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
    width: int
) -> StyleAndTextTuples:
    """生成可选 footer note 和 hint 的包裹片段。"""
    out: StyleAndTextTuples = []

    inner_width = max(1, int(width))

    if request.footer_note:
        out.extend(wrapped_text_fragments(
            request.footer_note,
            style="class:tui-menu.footer.note",
            width=inner_width,
        ))

    if request.footer_hint and request.allow_cancel:
        hint_fragments = _footer_hint_fragments(
            request.footer_hint,
            tone=request.footer_tone,
        )
        right_text, right_active = _footer_right_content(request)
        if right_text:

            right = clip_text(right_text, width=inner_width)
            right_width = get_cwidth(right)
            left_width = max(1, inner_width - right_width - 1)
            left = clip_fragments(hint_fragments, width=left_width)
            gap = max(
                1,
                inner_width - get_cwidth(fragments_text(left)) - right_width,
            )

            out.extend([
                *left,
                ("class:tui-menu.footer.hint", " " * gap),
                *_right_footer_fragments(right, active=right_active),
                ("", "\n"),
            ])

        else:
            for line in wrap_formatted_lines(
                    hint_fragments,
                    width=inner_width,
            ):
                out.extend(strip_leading_spaces(line))
                out.append(("", "\n"))

    return out


def _footer_hint_fragments(
    hint: MenuFooterValue,
    *,
    tone: MenuFooterTone,
) -> StyleAndTextTuples:
    """保留 footer 说明文字与按键标签的独立样式。"""
    hint_style = (
        "class:tui-menu.footer.secondary"
        if tone in {MenuFooterTone.SECONDARY, MenuFooterTone.KEY_EMPHASIS}
        else "class:tui-menu.footer.hint"
    )
    key_style = (
        "class:terminal.primary bold"
        if tone is MenuFooterTone.KEY_EMPHASIS
        else "class:tui-menu.footer.secondary"
        if tone is MenuFooterTone.SECONDARY
        else "class:tui-menu.footer.key"
    )
    if not isinstance(hint, MenuFooterHint):
        return [(hint_style, hint)]

    out: StyleAndTextTuples = [
        (hint_style, hint.prefix),
    ]
    for command_index, command in enumerate(hint.commands):
        if command_index:
            out.append((hint_style, hint.separator))
        for label_index, label in enumerate(command.key_labels):
            if label_index:
                out.append((hint_style, " or "))
            out.append((key_style, label))
        if command.description:
            out.append((
                hint_style,
                f" {command.description}",
            ))
    return out


def _right_footer_fragments(
    text: str,
    *,
    active: str = ""
) -> StyleAndTextTuples:
    """生成右侧模式栏并高亮当前模式。"""
    if not active or active not in text:
        return [("class:tui-menu.footer.right", text)]

    start = text.index(active)
    end = start + len(active)

    return [
        ("class:tui-menu.footer.right", text[:start]),
        ("class:tui-menu.footer.right.current", text[start:end]),
        ("class:tui-menu.footer.right", text[end:]),
    ]


def _footer_right_content(request: MenuRequest) -> tuple[str, str]:
    """返回 footer 右侧模式文本及当前模式标记。"""
    if not request.tabs:
        return request.footer_right, request.footer_right_active

    active_id = request.active_tab_id
    active_label = ""

    labels: list[str] = []

    for tab in request.tabs:
        active = tab.tab_id == active_id
        if active:
            active_label = f"[{tab.label}]"
            labels.append(active_label)
        else:
            labels.append(tab.label)

    return "  ".join(labels), active_label


def _inset_footer_fragments(
    request: MenuRequest,
    *,
    width: int,
    inset: int
) -> StyleAndTextTuples:
    """按共享 surface inset 生成 footer 内容。"""
    content_width = max(1, int(width) - max(0, int(inset)))
    return surface_inset_fragments(
        _request_footer_fragments(request, width=content_width),
        inset=max(0, int(inset)),
    )


def _selected_option(state: MenuState) -> MenuOption | None:
    """返回当前菜单选中项。"""
    if 0 <= state.selected < len(state.request.options):
        return state.request.options[state.selected]
    return None


if __name__ == '__main__':
    pass
