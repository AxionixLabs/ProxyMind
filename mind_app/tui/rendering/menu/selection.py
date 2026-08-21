# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from ...contracts.menu import (
    MenuOption,
    MenuRequest
)
from .state import MenuState

NO_SELECTION = object()


def selected_value(state: MenuState) -> typing.Any:
    """返回当前选项的稳定值，未选中时返回内部哨兵。"""
    options = state.request.options
    if 0 <= state.selected < len(options):
        return options[state.selected].value
    return NO_SELECTION


def same_value(left: typing.Any, right: typing.Any) -> bool:
    """比较菜单选项值并避免异常值破坏刷新。"""
    if left is right:
        return True
    try:
        result = left == right
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return result if isinstance(result, bool) else False


def generation_is_current(
    state: MenuState | None,
    request: MenuRequest,
    *,
    session_id: int | None = None,
) -> bool:
    """判断异步刷新是否没有落后于当前 view。"""
    return (
        state is not None
        and (session_id is None or state.session_id == session_id)
        and (
            request.generation <= 0
            or request.generation >= state.request.generation
        )
    )


def select_from_indices(
    options: tuple[MenuOption, ...],
    indices: tuple[int, ...],
    position: int,
    *,
    direction: int,
) -> int:
    """在过滤索引中选择下一项可执行选项。"""
    if not indices:
        return 0
    position %= len(indices)
    direction = 1 if direction >= 0 else -1
    for offset in range(len(indices)):
        index = indices[(position + direction * offset) % len(indices)]
        if not option_is_disabled(options[index]):
            return index
    return indices[position]


def select_from_indices_clamped(
    options: tuple[MenuOption, ...],
    indices: tuple[int, ...],
    position: int,
    *,
    direction: int,
) -> int:
    """在不跨越边界的情况下选择最近可执行项。"""
    if not indices:
        return 0
    position = min(max(position, 0), len(indices) - 1)
    direction = 1 if direction >= 0 else -1
    candidates = (
        range(position, len(indices))
        if direction > 0
        else range(position, -1, -1)
    )
    for candidate in candidates:
        if not option_is_disabled(options[indices[candidate]]):
            return indices[candidate]
    fallback = (
        range(position - 1, -1, -1)
        if direction > 0
        else range(position + 1, len(indices))
    )
    for candidate in fallback:
        if not option_is_disabled(options[indices[candidate]]):
            return indices[candidate]
    return indices[position]


def normalized_selection(
    options: tuple[MenuOption, ...],
    selected: int,
    *,
    step: int = 1,
) -> int:
    """把选中位置调整到可执行项，全部禁用时保留合法位置。"""
    if not options:
        return 0
    count = len(options)
    selected %= count
    direction = 1 if step >= 0 else -1
    for offset in range(count):
        index = (selected + direction * offset) % count
        if not option_is_disabled(options[index]):
            return index
    return selected


def option_detail(option: MenuOption, *, active: bool = False) -> str:
    """返回选项当前状态下的辅助说明。"""
    detail = (
        option.selected_detail
        if active and option.selected_detail
        else option.detail
    ) or option.disabled_reason
    suffix = ""
    if option.is_current:
        suffix = " (current)"
    elif option.is_default:
        suffix = " (default)"
    return f"{detail}{suffix}" if detail or suffix else ""


def option_is_disabled(option: MenuOption) -> bool:
    """判断选项是否因显式状态或原因而不可执行。"""
    return option.disabled or bool(option.disabled_reason)


def delete_previous_query_word(query: str) -> str:
    """删除搜索词尾部的一个词及其前置空白。"""
    index = len(query.rstrip())
    while index > 0 and not query[index - 1].isspace():
        index -= 1
    while index > 0 and query[index - 1].isspace():
        index -= 1
    return query[:index]


def filtered_indices(state: MenuState) -> tuple[int, ...]:
    """返回当前查询对应的原始选项索引。"""
    options = state.request.options
    if not state.request.searchable or not state.query:
        return tuple(range(len(options)))
    needle = state.query.casefold()
    return tuple(
        index
        for index, option in enumerate(options)
        if needle in (
            option.search_value or f"{option.label} {option.detail}"
        ).casefold()
    )


def has_selectable(
    options: tuple[MenuOption, ...],
    indices: tuple[int, ...],
) -> bool:
    """判断过滤结果中是否存在可执行选项。"""
    return any(not option_is_disabled(options[index]) for index in indices)


def initial_selection(
    options: tuple[MenuOption, ...],
    selected: int,
) -> int:
    """首次打开菜单时优先选择 current 或 default 项。"""
    for attribute in ("is_current", "is_default"):
        for index, option in enumerate(options):
            if getattr(option, attribute) and not option_is_disabled(option):
                return index
    return normalized_selection(options, selected)


def selection_for_request(
    options: tuple[MenuOption, ...],
    previous_selected: int,
    previous_value: typing.Any,
) -> int:
    """刷新选项后优先恢复同一 value，再按原位置归一化。"""
    if previous_value is not NO_SELECTION:
        for index, option in enumerate(options):
            if same_value(option.value, previous_value):
                return index
    return normalized_selection(options, previous_selected)


def visible_window(
    state: MenuState,
    *,
    visible_rows: int,
) -> tuple[int, tuple[int, ...]]:
    """返回当前查询下可见窗口的起始位置和原始索引。"""
    indices = filtered_indices(state)
    limit = max(1, int(visible_rows))
    if state.request.show_all_options or len(indices) <= limit:
        return 0, indices
    selected_position = (
        indices.index(state.selected)
        if state.selected in indices
        else 0
    )
    half = limit // 2
    start = max(
        0,
        min(selected_position - half, len(indices) - limit),
    )
    return start, indices[start:start + limit]


def visible_options(
    state: MenuState,
    *,
    visible_rows: int,
) -> tuple[int, tuple[MenuOption, ...]]:
    """返回围绕当前选择位置的菜单选项窗口。"""
    start, indices = visible_window(state, visible_rows=visible_rows)
    return start, tuple(state.request.options[index] for index in indices)


def moved_selection(state: MenuState, step: int) -> int | None:
    """计算菜单移动后的选项索引，无法移动时返回空值。"""
    indices = filtered_indices(state)
    if not has_selectable(state.request.options, indices):
        return None
    position = indices.index(state.selected) if state.selected in indices else 0
    if abs(step) <= 1:
        target = (position + step) % len(indices)
        return select_from_indices(
            state.request.options,
            indices,
            target,
            direction=step,
        )
    target = min(max(position + step, 0), len(indices) - 1)
    return select_from_indices_clamped(
        state.request.options,
        indices,
        target,
        direction=step,
    )


def selection_at(
    state: MenuState,
    selected: int,
    *,
    direction: int = 1,
) -> int | None:
    """计算距离指定绝对索引最近的可执行选项。"""
    options = state.request.options
    if not options:
        return None
    indices = filtered_indices(state)
    if not has_selectable(options, indices):
        return None
    if selected <= 0:
        position = 0
    elif selected >= len(options) - 1:
        position = len(indices) - 1
    else:
        position = min(
            range(len(indices)),
            key=lambda index: abs(indices[index] - selected),
        )
    return select_from_indices(
        options,
        indices,
        position,
        direction=direction,
    )


def normalized_filtered_selection(state: MenuState) -> int:
    """返回查询变化后过滤结果中的稳定选项索引。"""
    indices = filtered_indices(state)
    if not has_selectable(state.request.options, indices):
        return 0
    position = indices.index(state.selected) if state.selected in indices else 0
    return select_from_indices(
        state.request.options,
        indices,
        position,
        direction=1,
    )


if __name__ == '__main__':
    pass
