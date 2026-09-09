# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import (
    UIContent,
    UIControl
)
from prompt_toolkit.utils import get_cwidth

TokenMenuKind = typing.Literal[
    "command",
    "skill",
    "skill-mention",
    "plugin-mention",
    "file-mention",
    "directory-mention",
    "completion"
]

TOKEN_MENU_LEFT_PADDING = 2
TOKEN_MENU_META_WIDTH_NUMERATOR = 7
TOKEN_MENU_META_WIDTH_DENOMINATOR = 10

MENTION_MENU_KINDS = frozenset({
    "skill-mention",
    "plugin-mention",
    "file-mention",
    "directory-mention",
})


@dataclass(frozen=True, slots=True)
class TokenMenuItem(object):
    """描述一条输入 token 菜单候选。"""
    display_text: str
    meta_text: str
    kind: TokenMenuKind
    match_indices: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class TokenMenuSnapshot(object):
    """保存当前输入 token 菜单快照。"""
    items: tuple[TokenMenuItem, ...]
    selected: int = 0

    @property
    def normalized_selected(self) -> int:
        """返回落在候选范围内的选中位置。"""
        if not self.items:
            return 0
        return min(max(0, self.selected), len(self.items) - 1)


@dataclass(frozen=True, slots=True)
class DismissedToken(object):
    """记录一次已关闭补全的 token 身份。"""
    token: str
    occurrence: int

    @classmethod
    def capture(
        cls,
        text: str,
        token: str,
        token_start: int,
    ) -> "DismissedToken":
        """根据当前文本捕获 token 的稳定身份。"""
        return cls(
            token=token,
            occurrence=_token_occurrences_before(text, token, token_start),
        )

    def matches(self, text: str, token: str, token_start: int) -> bool:
        """判断当前 token 是否仍然对应同一次关闭状态。"""
        return (
            token == self.token
            and _token_occurrences_before(text, token, token_start)
            == self.occurrence
        )


class TokenMenuState(object):
    """保存输入 token 菜单的主动关闭状态。"""
    _dismissed_command_token: str | None
    _dismissed_skill_token: DismissedToken | None

    def __init__(self) -> None:
        self._dismissed_command_token = None
        self._dismissed_skill_token = None

    def command_dismissal_token(self) -> str | None:
        """返回已关闭的命令 token。"""
        return self._dismissed_command_token

    def dismiss_command(self, token: str) -> None:
        """记录已关闭的命令 token。"""
        self._dismissed_command_token = token

    def clear_command_dismissal(self) -> None:
        """清除命令 token 的关闭状态。"""
        self._dismissed_command_token = None

    def dismissed_skill(self) -> DismissedToken | None:
        """返回已关闭的 skill token。"""
        return self._dismissed_skill_token

    def dismiss_skill(self, token: DismissedToken) -> None:
        """记录已关闭的 skill token。"""
        self._dismissed_skill_token = token


def _token_occurrences_before(text: str, token: str, before: int) -> int:
    """统计指定位置之前完整 token 的出现次数。"""
    if not token:
        return 0

    count: int = 0
    start: int = 0
    limit: int = max(0, min(before, len(text)))

    while True:
        start = text.find(token, start, limit)
        if start < 0:
            return count

        end = start + len(token)
        if (
            (start == 0 or text[start - 1].isspace())
            and (end == len(text) or text[end].isspace())
        ):
            count += 1

        start += 1


def _fit_text(text: str, width: int) -> str:
    """返回适合指定显示宽度的文本。"""
    if width <= 0:
        return ""
    if get_cwidth(text) <= width:
        return text
    if width <= 3:
        return "." * width

    parts: list[str] = []

    used: int = 0
    limit: int = width - 3

    for char in text:
        char_width = get_cwidth(char)
        if used + char_width > limit:
            break
        parts.append(char)
        used += char_width
    return "".join(parts) + "..."


def _field(text: str, width: int) -> str:
    """返回按显示宽度填充后的字段。"""
    fitted = _fit_text(text, max(0, width))
    return fitted + " " * max(0, width - get_cwidth(fitted))


def _take_display_width(text: str, width: int) -> tuple[str, str]:
    """从文本开头取出不超过指定显示宽度的部分。"""
    limit = max(0, int(width))
    used = 0
    end = 0

    for index, char in enumerate(str(text)):
        char_width = max(0, get_cwidth(char))
        if used + char_width > limit:
            break
        used += char_width
        end = index + 1

    value = str(text)
    if end == 0 and value:
        end = 1
    return value[:end], value[end:]


def _wrap_display_text(text: str, width: int) -> tuple[str, ...]:
    """按终端显示宽度拆分说明文本，并优先在空格处换行。"""
    limit = max(1, int(width))
    remaining = " ".join(str(text or "").split())
    if not remaining:
        return ("",)

    rows: list[str] = []
    while remaining:
        head, tail = _take_display_width(remaining, limit)
        if not tail:
            rows.append(head)
            break

        break_at = head.rfind(" ")
        if break_at > 0:
            row = head[:break_at].rstrip()
            remaining = remaining[break_at + 1:].lstrip()
        else:
            row = head
            remaining = tail

        if not row:
            row, remaining = _take_display_width(remaining, limit)
        rows.append(row)

    return tuple(rows)


def _skill_meta_text(meta_text: str) -> str:
    """返回 `$` skill 菜单使用的带中括号类别说明。"""
    text = str(meta_text or "").strip()
    if not text or text.startswith("["):
        return text

    category, separator, detail = text.partition(" ")
    if not separator:
        return f"[{category}]"
    return f"[{category}] {detail}".rstrip()


def token_menu_display_height(
    snapshot: TokenMenuSnapshot | None,
    width: int,
) -> int:
    """返回当前 token 菜单的显示行数。"""
    if snapshot is None or not snapshot.items:
        return 0

    control = TokenCompletionMenuControl(lambda: snapshot)
    return control.display_height(width)


def prefix_match_indices(
    text: str,
    query: str,
    *,
    offset: int = 0
) -> tuple[int, ...] | None:
    """返回前缀匹配在显示文本中的字符位置。"""
    folded_text = str(text or "").casefold()
    folded_query = str(query or "").casefold()

    if not folded_query:
        return None

    start = max(0, offset)

    if folded_text[start:].startswith(folded_query):
        return tuple(range(start, start + len(query)))

    return None


def subsequence_match_indices(
    text: str,
    query: str
) -> tuple[int, ...] | None:
    """返回非连续匹配在显示文本中的字符位置。"""
    folded_text = str(text or "").casefold()
    folded_query = str(query or "").casefold()

    if not folded_query:
        return None

    indices: list[int] = []
    cursor: int = 0

    for char in folded_query:
        index = folded_text.find(char, cursor)
        if index < 0:
            return None
        indices.append(index)
        cursor = index + 1

    return tuple(indices)


class TokenCompletionMenuControl(UIControl):
    """绘制输入 token 补全菜单。"""

    MIN_WIDTH = 8

    def __init__(
        self,
        snapshot: typing.Callable[[], TokenMenuSnapshot | None]
    ) -> None:
        self._snapshot = snapshot

    @staticmethod
    def _meta_width(
        max_width: int,
        items: tuple[TokenMenuItem, ...],
        *,
        total_width: int,
        ensure_content_width: bool = False,
    ) -> int:
        """返回候选说明列宽。"""
        if max_width <= 0:
            return 0

        meta_texts = [
            (
                _skill_meta_text(item.meta_text)
                if item.kind == "skill"
                else item.meta_text
            )
            for item in items
            if item.meta_text
        ]
        if not meta_texts:
            return 0

        if any(item.kind in MENTION_MENU_KINDS for item in items):
            return max_width

        meta_limit = (
            max(0, total_width)
            * TOKEN_MENU_META_WIDTH_NUMERATOR
            // TOKEN_MENU_META_WIDTH_DENOMINATOR
        )

        content_width = max(get_cwidth(text) for text in meta_texts) + 2
        if ensure_content_width:
            meta_limit = max(meta_limit, content_width)

        return min(max_width, meta_limit, content_width)

    @staticmethod
    def _field_fragments(
        text: str,
        width: int,
        base_style: str,
        match_indices: tuple[int, ...] | None
    ) -> StyleAndTextTuples:
        """返回按显示宽度整理并高亮命中的字段片段。"""
        fitted = _fit_text(text, max(0, width))
        if not fitted and width <= 0:
            return []

        match_positions = frozenset(match_indices or ())

        fragments: StyleAndTextTuples = []

        current_style: str | None = None
        current_text: list[str] = []

        def flush() -> None:
            if current_text:
                fragments.append((current_style or base_style, "".join(current_text)))

        for index, char in enumerate(fitted):
            style = base_style
            if index in match_positions:
                style = f"{base_style} bold"
            if style != current_style:
                flush()
                current_style = style
                current_text = [char]
            else:
                current_text.append(char)

        flush()

        padding = max(0, width - get_cwidth(fitted))
        if padding > 0:
            fragments.append((base_style, " " * padding))

        return fragments

    def _name_fragments(
        self,
        item: TokenMenuItem,
        *,
        current: bool,
        main_width: int
    ) -> StyleAndTextTuples:
        """返回候选名称列的渲染片段。"""
        suffix = ".current" if current else ""
        item_style = f"class:token-menu.{item.kind}{suffix}"
        display_width = max(0, main_width - TOKEN_MENU_LEFT_PADDING - 1)

        marker = "> " if current and item.kind in {
            "skill-mention",
            "plugin-mention",
            "file-mention",
            "directory-mention",
        } else "  "

        if item.match_indices:
            fragments: StyleAndTextTuples = [
                (item_style, marker),
            ]
            fragments.extend(
                self._field_fragments(
                    item.display_text,
                    display_width,
                    item_style,
                    item.match_indices,
                )
            )
            fragments.append((item_style, " "))
            return fragments

        display = _field(item.display_text, display_width)
        return [(item_style, f"{marker}{display} ")]

    def _main_width(
        self,
        max_width: int,
        items: tuple[TokenMenuItem, ...]
    ) -> int:
        """返回候选名称列宽。"""
        max_display_width = max(
            get_cwidth(item.display_text)
            for item in items
        )
        return min(
            max_width,
            max(self.MIN_WIDTH, max_display_width + TOKEN_MENU_LEFT_PADDING + 1),
        )

    def _line_fragments(
        self,
        item: TokenMenuItem,
        *,
        current: bool,
        main_width: int,
        meta_width: int
    ) -> StyleAndTextTuples:
        """返回单行候选片段。"""
        suffix = ".current" if current else ""
        item_style = f"class:token-menu.{item.kind}{suffix}"
        meta_style = f"class:token-menu.meta.{item.kind}{suffix}"

        fragments = list(self._name_fragments(
            item,
            current=current,
            main_width=main_width,
        ))

        if meta_width > 0:
            if item.kind in {"skill-mention", "plugin-mention"} and item.meta_text:
                category, _separator, detail = item.meta_text.partition(" ")

                detail_width = max(
                    0,
                    meta_width - get_cwidth(category) - 4,
                )
                category_style = (
                    item_style
                    if item.kind == "plugin-mention"
                    else meta_style
                )
                fragments.extend([
                    (
                        meta_style,
                        f" {_field(detail, detail_width)}  ",
                    ),
                    (category_style, f"{category} "),
                ])
            elif item.kind in {"file-mention", "directory-mention"}:
                meta = item.meta_text.strip()
                category = meta.rsplit(None, 1)[-1] if meta else ""
                parent = meta[:-(len(category) + 1)] if category else meta
                inner_width = max(0, meta_width - 2)

                category_width = min(
                    get_cwidth(category),
                    max(0, inner_width - 1),
                )

                parent_width = max(0, inner_width - category_width - 1)
                category_style = item_style

                fragments.extend([
                    (meta_style, " "),
                    (meta_style, _field(parent, parent_width)),
                    (meta_style, " "),
                    (category_style, _field(category, category_width)),
                    (meta_style, " "),
                ])
            else:
                meta = (
                    _skill_meta_text(item.meta_text)
                    if item.kind == "skill"
                    else item.meta_text
                )
                meta = _field(meta, max(0, meta_width - 2))
                fragments.append((meta_style, f" {meta} "))

        return fragments

    def _row_fragments(
        self,
        item: TokenMenuItem,
        *,
        current: bool,
        main_width: int,
        meta_width: int,
        total_width: int
    ) -> list[StyleAndTextTuples]:
        """返回一项候选在当前宽度下占用的全部渲染行。"""
        if item.kind != "command" or not item.meta_text:
            return [self._line_fragments(
                item,
                current=current,
                main_width=main_width,
                meta_width=meta_width,
            )]

        content_width = max(
            1,
            min(
                max(0, meta_width - 2),
                max(1, total_width - main_width - 1),
            ),
        )
        if get_cwidth(item.meta_text) <= content_width:
            return [self._line_fragments(
                item,
                current=current,
                main_width=main_width,
                meta_width=meta_width,
            )]

        suffix = ".current" if current else ""
        meta_style = f"class:token-menu.meta.{item.kind}{suffix}"
        rows = _wrap_display_text(item.meta_text, content_width)

        first = list(self._name_fragments(
            item,
            current=current,
            main_width=main_width,
        ))
        first.append((meta_style, f" {_field(rows[0], content_width)} "))

        return [
            first,
            *[
                [
                    (meta_style, " " * main_width),
                    (meta_style, f" {row}"),
                ]
                for row in rows[1:]
            ],
        ]

    def _layout_widths(
        self,
        width: int,
        items: tuple[TokenMenuItem, ...]
    ) -> tuple[int, int]:
        """根据当前窗口宽度计算名称列和说明列宽度。"""
        main_width = self._main_width(width, items)
        meta_width = TokenCompletionMenuControl._meta_width(
            width - main_width,
            items,
            total_width=width,
            ensure_content_width=True,
        )

        return main_width, meta_width

    def display_height(self, width: int) -> int:
        """返回当前候选快照在指定宽度下的实际行数。"""
        snapshot = self._snapshot()
        if snapshot is None or not snapshot.items:
            return 0

        main_width, meta_width = self._layout_widths(width, snapshot.items)
        return sum(
            len(self._row_fragments(
                item,
                current=False,
                main_width=main_width,
                meta_width=meta_width,
                total_width=width,
            ))
            for item in snapshot.items
        )

    def preferred_width(
        self,
        max_available_width: int
    ) -> int | None:
        snapshot = self._snapshot()
        if snapshot is None or not snapshot.items:
            return 0

        if any(item.kind in MENTION_MENU_KINDS for item in snapshot.items):
            return max(0, int(max_available_width))

        main_width = self._main_width(max_available_width, snapshot.items)
        meta_width = TokenCompletionMenuControl._meta_width(
            max_available_width - main_width,
            snapshot.items,
            total_width=max_available_width,
        )

        return main_width + meta_width

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        wrap_lines: bool,
        get_line_prefix
    ) -> int | None:
        _ = max_available_height, wrap_lines, get_line_prefix
        snapshot = self._snapshot()
        if snapshot is None:
            return 0
        return token_menu_display_height(snapshot, width)

    def create_content(
        self,
        width: int,
        height: int
    ) -> UIContent:
        _ = height
        snapshot = self._snapshot()
        if snapshot is None or not snapshot.items:
            return UIContent()

        items = snapshot.items
        selected = snapshot.normalized_selected

        main_width, meta_width = self._layout_widths(width, items)

        rendered_lines: list[StyleAndTextTuples] = []

        selected_line: int = 0

        for index, item in enumerate(items):
            if index == selected:
                selected_line = len(rendered_lines)

            rendered_lines.extend(
                self._row_fragments(
                    item,
                    current=index == selected,
                    main_width=main_width,
                    meta_width=meta_width,
                    total_width=width,
                )
            )

        def get_line(idx: int) -> StyleAndTextTuples:
            return rendered_lines[idx]

        return UIContent(
            get_line=get_line,
            cursor_position=Point(x=0, y=selected_line),
            line_count=len(rendered_lines),
        )


if __name__ == '__main__':
    pass
