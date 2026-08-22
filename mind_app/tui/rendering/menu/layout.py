# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth
from ...contracts.menu import (
    MenuDescriptionLayout,
    MenuRequest
)
from ..fragments import (
    join_formatted_lines,
    split_formatted_lines
)


MENU_SURFACE_HORIZONTAL_INSET: typing.Final[int] = 2


def should_stack_description(
    request: MenuRequest,
    *,
    detail: str,
    available: int,
    label_width: int | None,
) -> bool:
    """判断当前选项是否需要把描述移到标签下一行。"""
    if request.description_layout is not MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW:
        return False
    if not detail:
        return False
    if label_width is None:
        return True
    separator_width = get_cwidth(request.description_separator)
    detail_width = available - label_width - separator_width
    return detail_width < max(1, request.min_description_width)


def prefix_width(number_width: int) -> int:
    """返回带序号候选行前缀的显示宽度。"""
    return get_cwidth(f"  › {'9' * max(1, number_width)}. ")


def search_prefix_width() -> int:
    """返回可搜索菜单不显示数字时的候选行前缀宽度。"""
    return get_cwidth("  › ")


def surface_content_width(width: int, *, inset: int) -> int:
    """返回扣除共享菜单表面左右内缩后的内容宽度。"""
    return max(1, int(width) - inset * 2)


def rows_width(width: int, *, inset: int = MENU_SURFACE_HORIZONTAL_INSET) -> int:
    """返回选项行可用的绘制宽度。"""
    return max(1, int(width) - max(0, int(inset)))


def surface_inset_fragments(
    fragments: StyleAndTextTuples,
    *,
    inset: int,
) -> StyleAndTextTuples:
    """为每个菜单内容行加入共享表面的左右内缩。"""
    lines = split_formatted_lines(fragments)
    if lines and not lines[-1]:
        lines.pop()
    return join_formatted_lines([
        [
            ("class:tui-menu.surface", " " * inset),
            *line,
        ]
        for line in lines
    ])


if __name__ == '__main__':
    pass
