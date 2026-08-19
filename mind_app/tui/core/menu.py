# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass, replace
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.terminal_text import (
    sanitize_terminal_line,
    sanitize_terminal_text,
)
from .models import (
    MenuColumnWidthMode,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    MenuTab,
    ViewCompletion
)
from .render import (
    clip_fragments,
    clip_text,
    join_formatted_lines,
    split_formatted_lines,
    wrap_formatted_lines,
)
from .view import BottomPaneViewStack, ViewIdentity

TUI_MENU_STYLE = Style.from_dict({
    # Keep menu text on the terminal's default foreground. Codex only adds
    # semantic emphasis here; the shared surface supplies the background.
    "tui-menu.title": "bold",
    "tui-menu.title.current": "ansicyan",
    "tui-menu.status": "dim",
    "tui-menu.status.current": "bold ansicyan",
    "tui-menu.surface": "",
    "tui-menu.help": "dim",
    "tui-menu.search": "",
    "tui-menu.search.placeholder": "dim",
    "tui-menu.tab": "dim",
    "tui-menu.tab-selected": "bold ansicyan",
    "tui-menu.index": "",
    "tui-menu.index.active": "bold ansicyan",
    "tui-menu.label": "",
    "tui-menu.label.active": "bold ansicyan",
    "tui-menu.detail": "dim",
    "tui-menu.body": "",
    "tui-menu.detail-selected": "bold ansicyan",
    "tui-menu.warning": "ansired",
    "tui-menu.label.disabled": "dim",
    "tui-menu.detail.disabled": "dim",
    "tui-menu.index.disabled": "dim",
    "tui-menu.footer": "dim",
    "tui-menu.footer.note": "dim",
    "tui-menu.footer.hint": "dim",
})

_NO_SELECTION = object()


@dataclass
class MenuState(object):
    """保存一个菜单 view 的请求、位置和等待结果。"""
    __slots__ = (
        "request",
        "future",
        "selected",
        "session_id",
        "dismiss_after_child_accept",
        "completion",
        "result",
        "query",
        "base_footer_hint",
    )
    request: MenuRequest
    future: asyncio.Future[typing.Any]
    selected: int
    session_id: int
    dismiss_after_child_accept: bool
    completion: ViewCompletion | None
    result: typing.Any
    query: str
    base_footer_hint: str


@dataclass(slots=True)
class _MenuView(object):
    """把单个菜单 frame 接入底部面板 view 契约。"""
    owner: "TuiMenu"
    state: MenuState

    @property
    def key_bindings(self) -> KeyBindings:
        return self.owner.key_bindings

    def fragments(self) -> StyleAndTextTuples:
        return self.owner.surface_fragments_for_state(self.state)

    def footer_fragments(self) -> StyleAndTextTuples:
        return self.owner.footer_fragments_for_state(self.state)

    def desired_height(self, width: int) -> int:
        return self.owner.content_height_for_state(self.state, width=width)

    def footer_height(self, width: int) -> int:
        return self.owner.footer_height_for_state(self.state, width=width)

    def view_id(self) -> str | None:
        return self.state.request.view_id

    def generation(self) -> int:
        return self.state.request.generation

    def session_id(self) -> int | None:
        return self.state.session_id

    def identity(self) -> ViewIdentity:
        return ViewIdentity(
            view_id=self.view_id(),
            generation=self.generation(),
            session_id=self.session_id(),
        )

    def selected_index(self) -> int | None:
        return self.owner.selected_index(self.state)

    def active_tab_id(self) -> str | None:
        return self.state.request.active_tab_id

    def handle_key_event(self, event: typing.Any) -> bool:
        """把按键交给菜单局部绑定处理。"""
        return self.owner.handle_key_event(event)

    def on_ctrl_c(self) -> bool:
        return self.owner.on_ctrl_c(self.state)

    def handle_paste(self, text: str) -> bool:
        return self.owner.handle_paste(text, self.state)

    def is_complete(self) -> bool:
        """判断菜单是否已经产生完成状态。"""
        return self.state.completion is not None

    def completion(self) -> ViewCompletion | None:
        return self.state.completion

    def result(self) -> typing.Any:
        return self.owner.result(self.state)

    def dismiss_after_child_accept(self) -> bool:
        """返回子菜单成功后是否继续关闭当前父菜单。"""
        return self.owner.state_dismisses_after_child_accept(self.state)

    def clear_dismiss_after_child_accept(self) -> None:
        """清除子菜单取消后遗留的父级关闭标记。"""
        self.owner.clear_state_child_dismissal(self.state)


class TuiMenu(object):
    """管理主 TUI Application 内的无边框选择菜单。"""

    VISIBLE_ROWS: typing.Final[int]             = 8
    SURFACE_HORIZONTAL_INSET: typing.Final[int] = 2
    SURFACE_VERTICAL_INSET: typing.Final[int]   = 1
    MIN_LABEL_WIDTH: typing.Final[int]          = 8
    MIN_DETAIL_WIDTH: typing.Final[int]         = 12
    MAX_DETAIL_RESERVE: typing.Final[int]       = 24

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_menu: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
        get_width: typing.Callable[[], int],
        view_stack: BottomPaneViewStack | None = None
    ) -> None:
        self.invalidate  = invalidate
        self.focus_menu  = focus_menu
        self.focus_input = focus_input
        self.get_width   = get_width

        self._view_stack = (
            view_stack
            if view_stack is not None
            else BottomPaneViewStack(changed=self._standalone_stack_changed)
        )
        self._generations: dict[str, int] = {}

        self._session_generation: int = 0

        self.key_bindings = self._build_key_bindings()

    @property
    def active(self) -> bool:
        """返回当前是否存在内嵌菜单。"""
        return bool(self._menu_views())

    @property
    def state(self) -> MenuState | None:
        """返回当前栈顶菜单状态。"""
        view = self._active_menu_view()
        return view.state if view is not None else None

    @property
    def active_session_id(self) -> int | None:
        """返回当前根菜单会话的单调标识。"""
        state = self.state
        return state.session_id if state is not None else None

    @staticmethod
    def state_dismisses_after_child_accept(state: MenuState) -> bool:
        """返回指定菜单状态的父级关闭标记。"""
        return state.dismiss_after_child_accept

    @staticmethod
    def clear_state_child_dismissal(state: MenuState) -> None:
        """清除指定菜单状态的父级关闭标记。"""
        state.dismiss_after_child_accept = False

    @staticmethod
    def _request_for_tab(
        request: MenuRequest,
        preferred_tab_id: str | None = None
    ) -> MenuRequest:
        """把请求切换到指定或默认页签的选项集合。"""
        if not request.tabs:
            return replace(request, active_tab_id=None)
        target_id = preferred_tab_id or request.active_tab_id
        tab = next(
            (tab for tab in request.tabs if tab.tab_id == target_id),
            request.tabs[0],
        )
        footer_hint = (
            tab.footer_hint
            if tab.footer_hint is not None
            else request.footer_hint
        )
        return replace(
            request,
            options=tab.options,
            active_tab_id=tab.tab_id,
            footer_hint=footer_hint,
        )

    @classmethod
    def _join_surface_sections(
        cls,
        header: StyleAndTextTuples,
        rows: StyleAndTextTuples,
        request: MenuRequest
    ) -> StyleAndTextTuples:
        """合并带普通内缩的头部与可向左回退的选项行。"""
        header_lines = split_formatted_lines(cls._surface_inset_fragments(header))
        row_lines = split_formatted_lines(rows)
        if row_lines and not row_lines[-1]:
            row_lines.pop()
        if request.option_rows_bleed_surface:
            row_lines = [
                [("class:tui-menu.surface", ""), *line]
                for line in row_lines
            ]
        else:
            row_lines = [
                [("class:tui-menu.surface", "  "), *line]
                for line in row_lines
            ]
        return join_formatted_lines([*header_lines, *row_lines])

    @staticmethod
    def _selected_value(state: MenuState) -> typing.Any:
        """返回当前选项的稳定值，未选中时返回内部哨兵。"""
        options = state.request.options
        if 0 <= state.selected < len(options):
            return options[state.selected].value
        return _NO_SELECTION

    @staticmethod
    def _same_value(left: typing.Any, right: typing.Any) -> bool:
        """比较菜单选项值并避免异常值破坏刷新。"""
        if left is right:
            return True
        try:
            result = left == right
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        return result if isinstance(result, bool) else False

    @staticmethod
    def _generation_is_current(
        state: MenuState | None,
        request: MenuRequest,
        *,
        session_id: int | None = None
    ) -> bool:
        """判断异步刷新是否没有落后于当前 view。"""
        return (
            state is not None
            and (
                session_id is None
                or state.session_id == session_id
            )
            and (
                request.generation <= 0
                or request.generation >= state.request.generation
            )
        )

    @staticmethod
    def _wrapped_text_fragments(
        text: str,
        *,
        style: str,
        width: int,
    ) -> StyleAndTextTuples:
        """把一段 footer 文本按内容宽度拆成带内缩的显示行。"""
        rows = wrap_formatted_lines([(style, text)], width=max(1, width))
        out: StyleAndTextTuples = []
        for row in rows:
            row = TuiMenu._strip_leading_spaces(row)
            out.append((style, "  "))
            out.extend(row)
            out.append(("", "\n"))
        return out

    @staticmethod
    def _strip_leading_spaces(
        row: StyleAndTextTuples
    ) -> StyleAndTextTuples:
        """移除包裹续行开头由断词保留的空白。"""
        out: StyleAndTextTuples = []
        strip = True
        for style, text in row:
            if strip:
                text = text.lstrip(" ")
                strip = not text
            if text:
                out.append((style, text))
        return out

    @staticmethod
    def _body_fragments(
        request: MenuRequest,
        *,
        width: int
    ) -> StyleAndTextTuples:
        """生成正文及可选的 Codex 风格警示尾段。"""
        out: StyleAndTextTuples = []

        warning_used: bool = False

        body_indent = "" if request.body_warning else "  "
        body_width  = width if request.body_warning else max(1, width - 2)

        for line in request.body:
            if request.body_warning and line and not warning_used:
                body_style = "class:tui-menu.body"
                rows = wrap_formatted_lines(
                    [
                        (body_style, line),
                        ("class:tui-menu.warning", f" {request.body_warning}"),
                    ],
                    width=max(1, body_width),
                )
                for row in rows:
                    out.extend([
                        (body_style, body_indent),
                        *row,
                        ("", "\n"),
                    ])
                warning_used = True
                continue
            out.extend([
                (
                    "class:tui-menu.detail",
                    f"{body_indent}{clip_text(line, width=body_width)}",
                ),
                ("", "\n"),
            ])
        return out

    @staticmethod
    def _header_fragments(
        request: MenuRequest,
        *,
        width: int
    ) -> StyleAndTextTuples:
        """生成标题和辅助状态的分层菜单头部。"""
        suffix = request.title_accent_suffix
        if suffix:
            title_text = (
                request.title[:-len(suffix)]
                if request.title.endswith(suffix)
                else request.title
            )
            suffix_width = get_cwidth(suffix)
            title = clip_text(
                title_text,
                width=max(0, width - suffix_width),
            )
            suffix = clip_text(suffix, width=max(0, width - get_cwidth(title)))
            out: StyleAndTextTuples = [
                ("class:tui-menu.title", title),
                ("class:tui-menu.title.current", suffix),
            ]
        else:
            out = [("class:tui-menu.title", clip_text(request.title, width=width))]

        if request.status:
            out.extend([
                ("", "\n"),
                (
                    (
                        "class:tui-menu.status.current"
                        if request.status.startswith("• ")
                        else "class:tui-menu.status"
                    ),
                    clip_text(request.status, width=width),
                ),
            ])

        return out

    @staticmethod
    def _tab_fragments(
        request: MenuRequest,
        *,
        width: int
    ) -> StyleAndTextTuples:
        """生成当前请求的单行分类页签。"""
        if not request.tabs:
            return []
        out: StyleAndTextTuples = []
        for index, tab in enumerate(request.tabs):
            if index:
                out.append(("class:tui-menu.tab", "  "))
            active = tab.tab_id == request.active_tab_id
            label = f"[{tab.label}]" if active else tab.label
            out.append((
                "class:tui-menu.tab-selected" if active else "class:tui-menu.tab",
                label,
            ))
        return clip_fragments(out, width=width)

    @staticmethod
    def _should_stack_description(
        request: MenuRequest,
        *,
        detail: str,
        available: int,
        label_width: int | None
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

    @staticmethod
    def _prefix_width(number_width: int) -> int:
        """返回带序号候选行前缀的显示宽度。"""
        return get_cwidth(f"  › {'9' * max(1, number_width)}. ")

    @staticmethod
    def _search_prefix_width() -> int:
        """返回可搜索菜单不显示数字时的候选行前缀宽度。"""
        return get_cwidth("  › ")

    @staticmethod
    def _select_from_indices(
        options: tuple[MenuOption, ...],
        indices: tuple[int, ...],
        position: int,
        *,
        direction: int
    ) -> int:
        """在过滤索引中选择下一项可执行选项。"""
        if not indices:
            return 0
        position %= len(indices)
        direction = 1 if direction >= 0 else -1
        for offset in range(len(indices)):
            index = indices[(position + direction * offset) % len(indices)]
            if not TuiMenu._option_is_disabled(options[index]):
                return index
        return indices[position]

    @staticmethod
    def _select_from_indices_clamped(
        options: tuple[MenuOption, ...],
        indices: tuple[int, ...],
        position: int,
        *,
        direction: int
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
            if not TuiMenu._option_is_disabled(options[indices[candidate]]):
                return indices[candidate]
        fallback = (
            range(position - 1, -1, -1)
            if direction > 0
            else range(position + 1, len(indices))
        )
        for candidate in fallback:
            if not TuiMenu._option_is_disabled(options[indices[candidate]]):
                return indices[candidate]
        return indices[position]

    @staticmethod
    def _normalized_selection(
        options: tuple[MenuOption, ...],
        selected: int,
        *,
        step: int = 1
    ) -> int:
        """把选中位置调整到可执行项，全部禁用时保留合法位置。"""
        if not options:
            return 0
        count = len(options)
        selected %= count
        direction = 1 if step >= 0 else -1
        for offset in range(count):
            index = (selected + direction * offset) % count
            if not TuiMenu._option_is_disabled(options[index]):
                return index
        return selected

    @staticmethod
    def _option_detail(
        option: MenuOption,
        *,
        active: bool = False
    ) -> str:
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

    @staticmethod
    def _option_is_disabled(option: MenuOption) -> bool:
        """判断选项是否因显式状态或原因而不可执行。"""
        return option.disabled or bool(option.disabled_reason)

    @staticmethod
    def _delete_previous_query_word(query: str) -> str:
        """删除搜索词尾部的一个词及其前置空白。"""
        index = len(query.rstrip())
        while index > 0 and not query[index - 1].isspace():
            index -= 1
        while index > 0 and query[index - 1].isspace():
            index -= 1
        return query[:index]

    @staticmethod
    def _filtered_indices(state: MenuState) -> tuple[int, ...]:
        """返回当前查询对应的原始选项索引。"""
        options = state.request.options
        if not state.request.searchable or not state.query:
            return tuple(range(len(options)))
        needle = state.query.casefold()
        return tuple(
            index
            for index, option in enumerate(options)
            if needle in (
                option.search_value
                or f"{option.label} {option.detail}"
            ).casefold()
        )

    @staticmethod
    def _has_selectable(
        options: tuple[MenuOption, ...],
        indices: tuple[int, ...]
    ) -> bool:
        """判断过滤结果中是否存在可执行选项。"""
        return any(
            not TuiMenu._option_is_disabled(options[index])
            for index in indices
        )

    @classmethod
    def _surface_content_width(cls, width: int) -> int:
        """返回扣除共享菜单表面左右内缩后的内容宽度。"""
        return max(1, int(width) - cls.SURFACE_HORIZONTAL_INSET * 2)

    @classmethod
    def _surface_inset_fragments(
        cls,
        fragments: StyleAndTextTuples
    ) -> StyleAndTextTuples:
        """为每个菜单内容行加入共享表面的左右内缩。"""
        lines = split_formatted_lines(fragments)
        if lines and not lines[-1]:
            lines.pop()
        return join_formatted_lines([
            [
                ("class:tui-menu.surface", " " * cls.SURFACE_HORIZONTAL_INSET),
                *line,
            ]
            for line in lines
        ])

    @classmethod
    def _initial_selection(
        cls,
        options: tuple[MenuOption, ...],
        selected: int
    ) -> int:
        """首次打开菜单时优先选择 current 或 default 项。"""
        for attribute in ("is_current", "is_default"):
            for index, option in enumerate(options):
                if (
                    getattr(option, attribute)
                    and not cls._option_is_disabled(option)
                ):
                    return index
        return cls._normalized_selection(options, selected)

    @classmethod
    def _selection_for_request(
        cls,
        options: tuple[MenuOption, ...],
        previous_selected: int,
        previous_value: typing.Any
    ) -> int:
        """刷新选项后优先恢复同一 value，再按原位置归一化。"""
        if previous_value is not _NO_SELECTION:
            for index, option in enumerate(options):
                if cls._same_value(option.value, previous_value):
                    return index
        return cls._normalized_selection(options, previous_selected)

    def active_view_id(self) -> str | None:
        """返回当前栈顶菜单的稳定标识。"""
        state = self.state
        return state.request.view_id if state is not None else None

    def session_is_active(self, session_id: int) -> bool:
        """判断指定菜单会话是否仍为当前会话。"""
        return self.active_session_id == session_id

    def view_id(self) -> str | None:
        """返回当前对象化菜单视图的稳定标识。"""
        return self.active_view_id()

    def completion(self) -> ViewCompletion | None:
        """返回当前菜单视图的完成状态。"""
        state = self.state
        return state.completion if state is not None else None

    def result(self, state: MenuState | None = None) -> typing.Any:
        """返回当前菜单视图的完成结果。"""
        current = state or self.state
        return current.result if current is not None else None

    def selected_index(self, state: MenuState | None = None) -> int | None:
        """返回指定菜单当前选中的原始选项索引。"""
        current = state or self.state
        return current.selected if current is not None else None

    def active_tab_id(self) -> str | None:
        """返回当前分类页签标识。"""
        state = self.state
        return state.request.active_tab_id if state is not None else None

    def on_ctrl_c(self, state: MenuState | None = None) -> bool:
        """处理菜单范围内的 Ctrl-C，并返回是否已消费。"""
        current = state or self.state
        if (
            current is None
            or current is not self.state
            or not current.request.allow_cancel
        ):
            return False
        self.cancel()
        return True

    def handle_paste(
        self,
        text: str,
        state: MenuState | None = None
    ) -> bool:
        """把粘贴文本交给可搜索菜单，并返回是否产生变化。"""
        current = state or self.state
        if (
            current is None
            or current is not self.state
            or not current.request.searchable
        ):
            return False
        pasted = sanitize_terminal_line(text)
        if not pasted:
            return False
        self._update_query(current.query + pasted)
        return True

    async def request(self, request: MenuRequest) -> typing.Any:
        """显示根菜单并等待其完成。"""
        if self.active:
            return await self.push(request)

        future = self.push(request)
        session_id = self.active_session_id
        try:
            return await future
        finally:
            if (
                session_id is not None
                and self.session_is_active(session_id)
            ):
                await self.close()

    async def close(self) -> None:
        """取消全部菜单并恢复输入焦点。"""
        while self.active:
            self._settle_current(None, ViewCompletion.CANCELLED)
        self.focus_input()
        self.invalidate()

    def push(self, request: MenuRequest) -> asyncio.Future[typing.Any]:
        """压入一个子菜单并返回只属于该 view 的 future。"""
        request = self._with_generation(_sanitize_menu_request(request))
        base_footer_hint = request.footer_hint
        request = self._request_for_tab(request)
        future = asyncio.get_running_loop().create_future()
        if not request.options and not request.body:
            future.set_result(None)
            return future

        selected = self._initial_selection(request.options, request.selected)
        session_id = self.active_session_id
        if session_id is None:
            self._session_generation += 1
            session_id = self._session_generation
        state = MenuState(
            request=request,
            future=future,
            selected=selected,
            session_id=session_id,
            dismiss_after_child_accept=False,
            completion=None,
            result=None,
            query="",
            base_footer_hint=base_footer_hint,
        )
        self._view_stack.push(_MenuView(self, state))
        return future

    def _switch_tab(self, step: int) -> None:
        """按方向切换页签并重置查询和选中项。"""
        state = self.state
        if state is None or len(state.request.tabs) < 2:
            return None
        tabs = state.request.tabs
        current = next(
            (
                index
                for index, tab in enumerate(tabs)
                if tab.tab_id == state.request.active_tab_id
            ),
            0,
        )
        target = tabs[(current + (1 if step >= 0 else -1)) % len(tabs)]
        request = self._request_for_tab(
            replace(state.request, footer_hint=state.base_footer_hint),
            target.tab_id,
        )
        state.request = request
        state.query = ""
        state.selected = self._initial_selection(request.options, request.selected)
        self.invalidate()

    def fragments(self) -> StyleAndTextTuples:
        """生成当前菜单可见窗口的格式化片段。"""
        state = self.state
        if state is None:
            return []

        return self._fragments(state)

    def surface_fragments_for_state(
        self,
        state: MenuState
    ) -> StyleAndTextTuples:
        """生成指定菜单状态的表面内容片段。"""
        return self._surface_fragments(state)

    def footer_fragments_for_state(
        self,
        state: MenuState,
        *,
        width: int | None = None
    ) -> StyleAndTextTuples:
        """生成指定菜单状态的透明页脚片段。"""
        return self._footer_fragments_for_state(state, width=width)

    def content_height_for_state(self, state: MenuState, *, width: int) -> int:
        """计算指定菜单状态的内容高度。"""
        return self._content_height(state, width=width)

    def footer_height_for_state(self, state: MenuState, *, width: int) -> int:
        """计算指定菜单状态的透明页脚高度。"""
        return self._footer_height(state, width=width)

    def _fragments(self, state: MenuState) -> StyleAndTextTuples:
        """生成指定菜单 frame 的格式化片段。"""

        surface = self._surface_fragments(state)
        footer = self._surface_footer_fragments_for_state(state)
        if footer:
            surface.append(("", "\n"))
            surface.extend([
                ("class:tui-menu.surface", "  "),
                ("", "\n"),
            ])
            surface.extend(self._surface_inset_fragments(footer))
        return surface

    def _surface_fragments(self, state: MenuState) -> StyleAndTextTuples:
        """生成指定菜单表面内的格式化片段，不包含透明 footer。"""

        request = state.request
        width = (
            self.get_width()
            if request.option_rows_bleed_surface
            else self._surface_content_width(self.get_width())
        )
        content_width = self._surface_content_width(self.get_width())

        _start, visible_indices = self._visible_indices(state)

        options = tuple(request.options[index] for index in visible_indices)
        number_width = self._enabled_number_width(state)
        prefix_width, label_width = self._row_layout(
            request,
            width=width,
            number_width=number_width,
            visible_indices=visible_indices,
        )

        header: StyleAndTextTuples = self._header_fragments(
            request,
            width=content_width,
        )

        header.append(("", "\n"))
        tabs = self._tab_fragments(request, width=width)
        if tabs:
            header.extend(tabs)
            header.append(("", "\n"))
        if request.help_text:
            header.extend([
                (
                    "class:tui-menu.help",
                    clip_text(request.help_text, width=width),
                ),
                ("", "\n"),
            ])

        header.extend(self._body_fragments(request, width=width))

        if request.searchable:
            query = state.query
            query_style = "class:tui-menu.search"
            if not query:
                query = request.search_placeholder
                query_style = "class:tui-menu.search.placeholder"
            header.extend([
                (
                    "class:tui-menu.search",
                    "  Search: ",
                ),
                (
                    query_style,
                    clip_text(query, width=max(1, width - 10)),
                ),
                ("", "\n"),
            ])

        rows_out: StyleAndTextTuples = []
        for offset, option in enumerate(options):
            index  = visible_indices[offset]
            active = index == state.selected and not self._option_is_disabled(option)
            if self._option_is_disabled(option):
                index_style = "class:tui-menu.index.disabled"
            else:
                index_style = (
                    "class:tui-menu.index.active"
                    if active
                    else "class:tui-menu.index"
                )

            prefix = self._option_prefix(
                state,
                index,
                active=active,
                number_width=number_width,
            )
            rows = self._option_fragments(
                option,
                available=max(1, width - get_cwidth(prefix)),
                label_width=label_width,
                active=active,
                request=request,
                prefix_width=prefix_width,
                width=width,
                index_style=index_style,
                prefix=prefix,
            )
            for row in rows:
                rows_out.extend(clip_fragments(row, width=width))
                rows_out.append(("", "\n"))

        return self._join_surface_sections(header, rows_out, request)

    def footer_fragments(self) -> StyleAndTextTuples:
        """生成当前菜单表面下方的透明 footer 片段。"""
        state = self.state
        return self._footer_fragments_for_state(state) if state is not None else []

    def _footer_fragments_for_state(
        self,
        state: MenuState,
        *,
        width: int | None = None,
    ) -> StyleAndTextTuples:
        """生成指定菜单 frame 的透明 footer 片段。"""
        render_width = self.get_width() if width is None else width
        return self._footer_fragments(
            state.request,
            width=max(1, int(render_width)),
        )

    def _surface_footer_fragments_for_state(
        self,
        state: MenuState,
        *,
        width: int | None = None,
    ) -> StyleAndTextTuples:
        """生成兼容独立菜单文本的 surface 内 footer 片段。"""
        render_width = self.get_width() if width is None else width
        return self._footer_fragments(
            state.request,
            width=self._surface_content_width(render_width),
        )

    def _footer_fragments(
        self,
        request: MenuRequest,
        *,
        width: int
    ) -> StyleAndTextTuples:
        """生成可选 footer note 和 hint 的包裹片段。"""
        out: StyleAndTextTuples = []
        inner_width = max(1, width - 2)

        if request.footer_note:
            out.extend(self._wrapped_text_fragments(
                request.footer_note,
                style="class:tui-menu.footer.note",
                width=inner_width,
            ))

        if request.footer_hint and request.allow_cancel:
            out.extend(self._wrapped_text_fragments(
                request.footer_hint,
                style="class:tui-menu.footer.hint",
                width=inner_width,
            ))

        return out

    def _option_fragments(
        self,
        option: MenuOption,
        *,
        available: int,
        label_width: int | None,
        active: bool,
        request: MenuRequest,
        prefix_width: int,
        width: int,
        index_style: str,
        prefix: str,
    ) -> list[StyleAndTextTuples]:
        """按可用宽度分配选项主标签和辅助信息。"""
        if self._option_is_disabled(option):
            label_style = "class:tui-menu.label.disabled"
            detail_style = "class:tui-menu.detail.disabled"
        else:
            label_style = (
                "class:tui-menu.label.active"
                if active
                else "class:tui-menu.label"
            )
            detail_style = (
                "class:tui-menu.detail-selected"
                if active
                else "class:tui-menu.detail"
            )
        detail = self._option_detail(option, active=active)
        if detail and self._should_stack_description(
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
            detail_width = max(1, width - prefix_width)
            for detail_row in wrap_formatted_lines(
                [(detail_style, detail)],
                width=detail_width,
            ):
                rows.append([
                    (detail_style, " " * prefix_width),
                    *self._strip_leading_spaces(detail_row),
                ])
            return rows

        if not detail or label_width is None:
            return [[
                (index_style, prefix),
                (label_style, clip_text(option.label, width=available)),
            ]]

        separator = request.description_separator
        separator_width = get_cwidth(separator)

        label        = clip_text(option.label, width=label_width)
        padding      = " " * max(0, label_width - get_cwidth(label))
        detail_width = available - label_width - separator_width

        return [[
            (index_style, prefix),
            (label_style, f"{label}{padding}"),
            (
                detail_style,
                f"{separator}{clip_text(detail, width=detail_width)}",
            ),
        ]]

    def _row_layout(
        self,
        request: MenuRequest,
        *,
        width: int,
        number_width: int,
        visible_indices: tuple[int, ...],
    ) -> tuple[int, int | None]:
        """计算当前窗口的选项前缀和共享标签列宽。"""
        prefix_width = (
            self._search_prefix_width()
            if request.searchable
            else self._prefix_width(number_width)
        )
        label_width = self._label_column_width(
            request,
            available=max(1, width - prefix_width),
            visible_indices=visible_indices,
        )
        return prefix_width, label_width

    def _enabled_number_width(self, state: MenuState) -> int:
        """返回当前过滤结果中可执行候选的序号宽度。"""
        count = sum(
            not self._option_is_disabled(state.request.options[index])
            for index in self._filtered_indices(state)
        )
        return len(str(max(1, count)))

    def _option_prefix(
        self,
        state: MenuState,
        index: int,
        *,
        active: bool,
        number_width: int,
    ) -> str:
        """生成候选项的选择标记和可执行序号 gutter。"""
        if state.request.option_rows_bleed_surface:
            marker = "›" if active else " "
        else:
            marker = "  ›" if active else "   "
        if state.request.searchable:
            return f"{marker} "
        option = state.request.options[index]
        if self._option_is_disabled(option):
            gutter_marker = clip_text(
                option.disabled_gutter_marker,
                width=number_width,
            )
            gutter = (
                f"{gutter_marker.rjust(number_width)}  "
                if gutter_marker
                else " " * (number_width + 2)
            )
            return f"{marker} {gutter}"
        enabled_indices = tuple(
            candidate
            for candidate in self._filtered_indices(state)
            if not self._option_is_disabled(state.request.options[candidate])
        )
        number = enabled_indices.index(index) + 1
        return f"{marker} {str(number).rjust(number_width)}. "

    def _label_column_width(
        self,
        request: MenuRequest,
        *,
        available: int,
        visible_indices: tuple[int, ...],
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
                get_cwidth(self._option_detail(option, active=True))
                for option in measurement_options
                if self._option_detail(option, active=True)
            ),
            default=0,
        )
        if detail_width <= 0:
            return None

        detail_reserve = min(
            detail_width,
            max(
                self.MIN_DETAIL_WIDTH,
                min(self.MAX_DETAIL_RESERVE, available // 3),
            ),
        )

        max_label_width = (
            available
            - get_cwidth(request.description_separator)
            - detail_reserve
        )
        if max_label_width < self.MIN_LABEL_WIDTH:
            return None

        if request.column_width_mode is MenuColumnWidthMode.FIXED:
            return max(
                self.MIN_LABEL_WIDTH,
                min(max_label_width, available * 3 // 10),
            )

        requested_width = request.name_column_width or 0
        return min(max(natural_label_width, requested_width), max_label_width)

    def height(self) -> int:
        """返回当前菜单占用的显示行数。"""
        state = self.state
        if state is None:
            return 0
        return self._height(state, width=self.get_width())

    def _height(self, state: MenuState, *, width: int) -> int:
        """返回指定菜单 frame 占用的显示行数。"""
        return (
            self._content_height(state, width=width)
            + int(bool(self._surface_footer_fragments_for_state(state, width=width)))
            + self._surface_footer_height(state, width=width)
        )

    def _content_height(self, state: MenuState, *, width: int) -> int:
        """返回指定菜单表面内容占用的显示行数。"""
        content = self._fragments_for_height(
            state,
            width=(
                width
                if state.request.option_rows_bleed_surface
                else self._surface_content_width(width)
            ),
        )
        lines = split_formatted_lines(content)
        if lines and not lines[-1]:
            lines.pop()
        return len(lines)

    def _footer_height(self, state: MenuState, *, width: int) -> int:
        """返回指定菜单透明 footer 占用的显示行数。"""
        footer = self._footer_fragments_for_state(state, width=width)
        lines = split_formatted_lines(footer)
        if lines and not lines[-1]:
            lines.pop()
        return len(lines)

    def _surface_footer_height(self, state: MenuState, *, width: int) -> int:
        """返回独立菜单文本中 surface 内 footer 的显示行数。"""
        footer = self._surface_footer_fragments_for_state(state, width=width)
        lines = split_formatted_lines(footer)
        if lines and not lines[-1]:
            lines.pop()
        return len(lines)

    def _fragments_for_height(
        self,
        state: MenuState,
        *,
        width: int,
    ) -> StyleAndTextTuples:
        """生成不含表面内缩、用于测量的菜单内容。"""
        request = state.request
        _start, visible_indices = self._visible_indices(state)
        options = tuple(request.options[index] for index in visible_indices)
        number_width = self._enabled_number_width(state)
        prefix_width, label_width = self._row_layout(
            request,
            width=width,
            number_width=number_width,
            visible_indices=visible_indices,
        )

        out = self._header_fragments(request, width=width)
        out.append(("", "\n"))
        tabs = self._tab_fragments(request, width=width)
        if tabs:
            out.extend(tabs)
            out.append(("", "\n"))
        if request.help_text:
            out.extend([
                ("class:tui-menu.help", clip_text(request.help_text, width=width)),
                ("", "\n"),
            ])
        out.extend(self._body_fragments(request, width=width))
        if request.searchable:
            query = state.query or request.search_placeholder
            out.extend([
                ("class:tui-menu.search", "  Search: "),
                ("class:tui-menu.search", clip_text(query, width=max(1, width - 10))),
                ("", "\n"),
            ])
        for offset, option in enumerate(options):
            index = visible_indices[offset]
            active = index == state.selected and not self._option_is_disabled(option)
            prefix = self._option_prefix(
                state,
                index,
                active=active,
                number_width=number_width,
            )
            if self._option_is_disabled(option):
                index_style = "class:tui-menu.index.disabled"
            else:
                index_style = (
                    "class:tui-menu.index.active"
                    if active
                    else "class:tui-menu.index"
                )
            for row in self._option_fragments(
                option,
                available=max(1, width - get_cwidth(prefix)),
                label_width=label_width,
                active=active,
                request=request,
                prefix_width=prefix_width,
                width=width,
                index_style=index_style,
                prefix=prefix,
            ):
                out.extend(clip_fragments(row, width=width))
                out.append(("", "\n"))
        return out

    def desired_height(self, width: int) -> int:
        """按底部面板协议返回菜单所需高度。"""
        state = self.state
        return self._height(state, width=width) if state is not None else 0

    def generation(self) -> int:
        """返回当前对象化菜单视图的刷新代数。"""
        state = self.state
        return state.request.generation if state is not None else 0

    def active_view_identity(self) -> ViewIdentity | None:
        """返回当前栈顶菜单的稳定身份快照。"""
        view = self._active_menu_view()
        return view.identity() if view is not None else None

    def update(self, request: MenuRequest) -> None:
        """替换当前菜单或只读面板内容。"""
        state = self.state
        if state is None:
            return None
        self._replace_state_request(state, request)
        self.invalidate()

    def _replace_state_request(
        self,
        state: MenuState,
        request: MenuRequest,
    ) -> None:
        """在不改变 view 对象的情况下替换其请求内容。"""
        previous_value = self._selected_value(state)
        previous_selected = state.selected
        request = self._with_generation(_sanitize_menu_request(request))
        state.base_footer_hint = request.footer_hint
        request = self._request_for_tab(
            request,
            preferred_tab_id=state.request.active_tab_id,
        )
        state.request = request
        if not request.searchable:
            state.query = ""
        if request.options:
            state.selected = self._selection_for_request(
                request.options,
                previous_selected,
                previous_value,
            )
        else:
            state.selected = 0
        self._normalize_filtered_state(state)

    def replace_active_if_id(
        self,
        view_id: str,
        request: MenuRequest,
        *,
        session_id: int | None = None,
    ) -> bool:
        """仅在栈顶标识匹配时替换菜单内容。"""
        if self.active_view_id() != view_id:
            return False
        if not self._generation_is_current(
            self.state,
            request,
            session_id=session_id,
        ):
            return False
        self.update(request)
        return True

    def replace_present_if_id(
        self,
        view_id: str,
        request: MenuRequest,
        *,
        session_id: int | None = None,
    ) -> bool:
        """替换栈中仍存在的指定菜单内容。"""
        view = self._view_stack.find(view_id, session_id=session_id)
        state = view.state if isinstance(view, _MenuView) else None
        if state is None:
            return False
        if not self._generation_is_current(
            state,
            request,
            session_id=session_id,
        ):
            return False
        self._replace_state_request(state, request)
        self.invalidate()
        return True

    def replace_present_many_if_id(
        self,
        updates: typing.Iterable[tuple[str, MenuRequest]],
        *,
        session_id: int | None = None,
    ) -> int:
        """在同一会话内原子刷新多个仍存在的菜单 view。"""
        prepared: list[tuple[MenuState, MenuRequest]] = []
        seen: set[str] = set()
        for view_id, request in updates:
            if view_id in seen:
                continue
            seen.add(view_id)
            view = self._view_stack.find(view_id, session_id=session_id)
            state = view.state if isinstance(view, _MenuView) else None
            if not self._generation_is_current(
                state,
                request,
                session_id=session_id,
            ):
                return 0
            prepared.append((state, request))

        for state, request in prepared:
            self._replace_state_request(state, request)
        if prepared:
            self.invalidate()
        return len(prepared)

    def _with_generation(self, request: MenuRequest) -> MenuRequest:
        """为带 view 标识的请求分配单调显示代数。"""
        view_id = request.view_id
        if not view_id:
            return request
        current = self._generations.get(view_id, 0)
        generation = (
            current + 1
            if request.generation <= 0
            else max(current, request.generation)
        )
        self._generations[view_id] = max(current, generation)
        if request.generation == generation:
            return request
        return replace(request, generation=generation)

    def dismiss_view_by_id(
        self,
        view_id: str,
        *,
        session_id: int | None = None,
    ) -> bool:
        """按标识取消当前菜单及其上方子菜单。"""
        return bool(
            self.dismiss_views_by_id((view_id,), session_id=session_id)
        )

    def dismiss_views_by_id(
        self,
        view_ids: typing.Iterable[str],
        *,
        session_id: int | None = None,
    ) -> int:
        """从最浅命中标识开始取消菜单及其全部子菜单。"""
        targets = {
            str(view_id).strip()
            for view_id in view_ids
            if str(view_id).strip()
        }
        views = self._menu_views()
        index = next(
            (
                index
                for index, view in enumerate(views)
                if (
                    view.state.request.view_id in targets
                    and (
                        session_id is None
                        or view.state.session_id == session_id
                    )
                )
            ),
            None,
        )
        if index is None:
            return 0
        dismissed = len(views) - index
        while (
            self._active_menu_view() is not None
            and len(self._menu_views()) > index
        ):
            self._settle_current(None, ViewCompletion.CANCELLED)
        if self.active:
            self.state.dismiss_after_child_accept = False
        return dismissed

    def finish(self, value: typing.Any) -> None:
        """以接受结果结束当前菜单。"""
        self._complete(self.state, value, ViewCompletion.ACCEPTED)

    def cancel(self) -> None:
        """取消当前菜单并回到父菜单或输入区。"""
        self._complete(self.state, None, ViewCompletion.CANCELLED)

    def handle_key_event(self, event: typing.Any) -> bool:
        """处理统一 view 协议传入的单个按键事件。"""
        state = self.state
        if state is None:
            return False
        key_sequence = getattr(event, "key_sequence", ())
        key = (
            key_sequence[-1].key
            if key_sequence
            else getattr(event, "key", None)
        )
        data = getattr(event, "data", "")

        if key in (Keys.Down, "down", Keys.ControlN, "c-n"):
            self._move(1)
        elif key in (Keys.Up, "up", Keys.ControlP, "c-p"):
            self._move(-1)
        elif key in (Keys.PageDown, "pagedown"):
            self._move(self.VISIBLE_ROWS)
        elif key in (Keys.PageUp, "pageup"):
            self._move(-self.VISIBLE_ROWS)
        elif key in (Keys.Home, "home"):
            self._set_selection(0, direction=1)
        elif key in (Keys.End, "end"):
            self._set_selection(
                len(state.request.options) - 1,
                direction=-1,
            )
        elif key in (Keys.Right, "right"):
            self._switch_tab(1)
        elif key in (Keys.Left, "left"):
            self._switch_tab(-1)
        elif key in (Keys.Backspace, "backspace") and state.request.searchable:
            self._update_query(state.query[:-1])
        elif key in (Keys.ControlU, "c-u") and state.request.searchable:
            self._update_query("")
        elif key in (Keys.ControlW, "c-w") and state.request.searchable:
            self._update_query(self._delete_previous_query_word(state.query))
        elif key in (Keys.BracketedPaste,) and state.request.searchable:
            self.handle_paste(data, state)
        elif (
            key in (Keys.Escape, "escape", "q")
            and state.request.allow_cancel
        ):
            self.cancel()
        elif key in (Keys.ControlC, "c-c"):
            return self.on_ctrl_c(state)
        elif key in (Keys.Enter, "enter"):
            indices = self._filtered_indices(state)
            if self._has_selectable(state.request.options, indices):
                self._choose_index(state.selected)
            else:
                self.cancel()
        elif data and data.isprintable() and state.request.searchable:
            self._update_query(state.query + data)
        elif data.isdigit() and data != "0":
            indices = tuple(
                index
                for index in self._filtered_indices(state)
                if not self._option_is_disabled(state.request.options[index])
            )
            selected_number = int(data)
            if selected_number <= len(indices):
                self._choose_index(indices[selected_number - 1])
        else:
            return False
        return True

    def _complete(
        self,
        state: MenuState | None,
        value: typing.Any,
        completion: ViewCompletion,
    ) -> None:
        """完成指定栈顶菜单，并按完成语义处理父级。"""
        if state is None or self.state is not state:
            return None

        self._settle_current(value, completion)

        if completion is ViewCompletion.ACCEPTED:
            while self.active and self.state.dismiss_after_child_accept:
                self._settle_current(value, ViewCompletion.ACCEPTED)
        elif self.active:
            self.state.dismiss_after_child_accept = False

    def _settle_current(
        self,
        value: typing.Any,
        completion: ViewCompletion,
    ) -> MenuState | None:
        """弹出栈顶菜单并完成其等待结果。"""
        state = self.state
        if state is None:
            return None
        state.completion = completion
        state.result = value
        view = self._active_menu_view()
        if view is None or not self._view_stack.pop(view):
            return None
        if not state.future.done():
            state.future.set_result(value)
        return state

    def _active_menu_view(self) -> _MenuView | None:
        """返回当前栈顶属于此控制器的菜单 view。"""
        view = self._view_stack.active_view
        if isinstance(view, _MenuView) and view.owner is self:
            return view
        return None

    def _menu_views(self) -> tuple[_MenuView, ...]:
        """返回此控制器当前持有的菜单 view。"""
        return tuple(
            view
            for view in self._view_stack.views
            if isinstance(view, _MenuView) and view.owner is self
        )

    def _standalone_stack_changed(self, active: bool) -> None:
        """为独立菜单实例同步焦点和重绘。"""
        if active:
            self.focus_menu()
        else:
            self.focus_input()
        self.invalidate()

    def _move(self, step: int) -> None:
        """移动当前菜单选择位置。"""
        state = self.state
        if state is None:
            return None
        indices = self._filtered_indices(state)
        if not self._has_selectable(state.request.options, indices):
            return None
        position = (
            indices.index(state.selected)
            if state.selected in indices
            else 0
        )
        if abs(step) <= 1:
            target = (position + step) % len(indices)
            state.selected = self._select_from_indices(
                state.request.options,
                indices,
                target,
                direction=step,
            )
        else:
            target = min(max(position + step, 0), len(indices) - 1)
            state.selected = self._select_from_indices_clamped(
                state.request.options,
                indices,
                target,
                direction=step,
            )
        self.invalidate()

    def _set_selection(self, selected: int, *, direction: int = 1) -> None:
        """把当前选择定位到指定索引并跳过禁用项。"""
        state = self.state
        if state is None or not state.request.options:
            return None
        indices = self._filtered_indices(state)
        if not self._has_selectable(state.request.options, indices):
            return None
        if selected <= 0:
            position = 0
        elif selected >= len(state.request.options) - 1:
            position = len(indices) - 1
        else:
            position = min(
                range(len(indices)),
                key=lambda index: abs(indices[index] - selected),
            )
        state.selected = self._select_from_indices(
            state.request.options,
            indices,
            position,
            direction=direction,
        )
        self.invalidate()

    def _update_query(self, query: str) -> None:
        """更新搜索查询并把选择定位到新的过滤结果。"""
        state = self.state
        if state is None or not state.request.searchable:
            return None
        state.query = query
        self._normalize_filtered_state(state)
        self.invalidate()

    def _normalize_filtered_state(self, state: MenuState) -> None:
        """在刷新或查询变化后把选中项限制在过滤结果内。"""
        indices = self._filtered_indices(state)
        if not self._has_selectable(state.request.options, indices):
            state.selected = 0
            return None
        position = (
            indices.index(state.selected)
            if state.selected in indices
            else 0
        )
        state.selected = self._select_from_indices(
            state.request.options,
            indices,
            position,
            direction=1,
        )

    def _visible_indices(
        self,
        state: MenuState,
    ) -> tuple[int, tuple[int, ...]]:
        """返回当前查询下可见窗口对应的原始索引。"""
        indices = self._filtered_indices(state)
        if len(indices) <= self.VISIBLE_ROWS:
            return 0, indices
        selected_position = (
            indices.index(state.selected)
            if state.selected in indices
            else 0
        )
        half = self.VISIBLE_ROWS // 2
        start = max(
            0,
            min(
                selected_position - half,
                len(indices) - self.VISIBLE_ROWS,
            ),
        )
        return start, indices[start:start + self.VISIBLE_ROWS]

    def _choose_index(self, index: int) -> None:
        """按绝对索引提交菜单选项。"""
        state = self.state
        if state is None or not (0 <= index < len(state.request.options)):
            return None
        if state.request.searchable and index not in self._filtered_indices(state):
            return None
        option = state.request.options[index]
        if self._option_is_disabled(option):
            return None
        if option.on_select is not None:
            option.on_select()
        if option.dismiss_on_select and self.state is state:
            self._complete(state, option.value, ViewCompletion.ACCEPTED)
        elif option.dismiss_parent_on_child_accept:
            state.dismiss_after_child_accept = True
        self.invalidate()

    def _visible_options(
        self,
        state: MenuState
    ) -> tuple[int, tuple[MenuOption, ...]]:
        """返回围绕当前选择位置的菜单窗口。"""
        start, indices = self._visible_indices(state)
        return start, tuple(state.request.options[index] for index in indices)

    def _build_key_bindings(self) -> KeyBindings:
        """创建内嵌菜单局部按键绑定。"""
        bindings = KeyBindings()

        @bindings.add("enter")
        def _(_event) -> None:
            state = self.state
            indices = self._filtered_indices(state) if state is not None else ()
            if (
                state is not None
                and self._has_selectable(state.request.options, indices)
            ):
                self._choose_index(state.selected)
            elif state is not None:
                self.cancel()

        @bindings.add("down")
        @bindings.add("c-n")
        def _(_event) -> None:
            self._move(1)

        @bindings.add("up")
        @bindings.add("c-p")
        def _(_event) -> None:
            self._move(-1)

        @bindings.add("pagedown")
        def _(_event) -> None:
            self._move(self.VISIBLE_ROWS)

        @bindings.add("pageup")
        def _(_event) -> None:
            self._move(-self.VISIBLE_ROWS)

        @bindings.add("home")
        def _(_event) -> None:
            self._set_selection(0)

        @bindings.add("end")
        def _(_event) -> None:
            state = self.state
            if state is not None:
                self._set_selection(
                    len(state.request.options) - 1,
                    direction=-1,
                )

        @bindings.add("right")
        def _(_event) -> None:
            self._switch_tab(1)

        @bindings.add("left")
        def _(_event) -> None:
            self._switch_tab(-1)

        @bindings.add("backspace")
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.searchable:
                self._update_query(state.query[:-1])

        @bindings.add("c-u")
        def _(_event) -> None:
            self._update_query("")

        @bindings.add("c-w")
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.searchable:
                self._update_query(
                    self._delete_previous_query_word(state.query),
                )

        @bindings.add(Keys.BracketedPaste)
        def _(event) -> None:
            state = self.state
            if state is not None and state.request.searchable:
                self.handle_paste(getattr(event, "data", ""), state)

        @bindings.add(Keys.Any)
        def _(event) -> None:
            state = self.state
            data = getattr(event, "data", "")
            if (
                state is not None
                and state.request.searchable
                and data
                and data.isprintable()
            ):
                self._update_query(state.query + data)

        @bindings.add(Keys.Escape, eager=True)
        @bindings.add("q")
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.allow_cancel:
                self.cancel()

        @bindings.add("c-c")
        def _(_event) -> None:
            self.on_ctrl_c()

        for number in range(1, 10):
            @bindings.add(str(number))
            def _(_event, selected_number=number) -> None:
                state = self.state
                if state is None:
                    return None
                if state.request.searchable:
                    self._update_query(state.query + str(selected_number))
                    return None
                indices = tuple(
                    index
                    for index in self._filtered_indices(state)
                    if not self._option_is_disabled(
                        state.request.options[index],
                    )
                )
                if selected_number <= len(indices):
                    self._choose_index(indices[selected_number - 1])

        return bindings


def _sanitize_menu_request(request: MenuRequest) -> MenuRequest:
    """复制菜单请求并清理其中的显示字段。"""
    return MenuRequest(
        title=sanitize_terminal_line(request.title),
        title_accent_suffix=_sanitize_inline_text(
            request.title_accent_suffix,
        ),
        options=_sanitize_menu_options(request.options),
        body=tuple(sanitize_terminal_line(line) for line in request.body),
        body_warning=sanitize_terminal_line(request.body_warning),
        option_rows_bleed_surface=request.option_rows_bleed_surface,
        selected=request.selected,
        status=sanitize_terminal_line(request.status),
        help_text=sanitize_terminal_line(request.help_text),
        view_id=sanitize_terminal_line(request.view_id or "") or None,
        generation=max(0, int(request.generation)),
        searchable=request.searchable,
        search_placeholder=sanitize_terminal_line(request.search_placeholder),
        footer_note=sanitize_terminal_line(request.footer_note),
        footer_hint=sanitize_terminal_line(request.footer_hint),
        allow_cancel=request.allow_cancel,
        description_layout=_sanitize_description_layout(request.description_layout),
        description_separator=_sanitize_inline_text(
            request.description_separator,
        ),
        min_description_width=max(1, int(request.min_description_width)),
        tabs=tuple(
            _sanitize_menu_tab(tab)
            for tab in request.tabs
        ),
        active_tab_id=(
            sanitize_terminal_line(request.active_tab_id or "") or None
        ),
        column_width_mode=_sanitize_column_width_mode(
            request.column_width_mode,
        ),
        name_column_width=(
            max(1, int(request.name_column_width))
            if request.name_column_width is not None
            else None
        ),
    )


def _sanitize_description_layout(value: typing.Any) -> MenuDescriptionLayout:
    """清理菜单描述排列策略并回退到默认模式。"""
    try:
        return MenuDescriptionLayout(value)
    except (TypeError, ValueError):
        return MenuDescriptionLayout.COLUMNS


def _sanitize_inline_text(value: typing.Any) -> str:
    """清理单行显示文本并保留其布局空白。"""
    raw = sanitize_terminal_text(value)
    if not raw:
        return ""
    return raw.replace("\n", " ")


def _sanitize_column_width_mode(value: typing.Any) -> MenuColumnWidthMode:
    """清理菜单列宽模式并回退到稳定默认值。"""
    try:
        return MenuColumnWidthMode(value)
    except (TypeError, ValueError):
        return MenuColumnWidthMode.AUTO_ALL_ROWS


def _sanitize_menu_tab(tab: MenuTab) -> MenuTab:
    """清理菜单页签的标识、标题、选项和提示文本。"""
    return MenuTab(
        tab_id=sanitize_terminal_line(tab.tab_id),
        label=sanitize_terminal_line(tab.label),
        options=_sanitize_menu_options(tab.options),
        footer_hint=(
            sanitize_terminal_line(tab.footer_hint)
            if tab.footer_hint is not None
            else None
        ),
    )


def _sanitize_menu_option(option: MenuOption) -> MenuOption:
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
        disabled_gutter_marker=sanitize_terminal_line(
            option.disabled_gutter_marker,
        ),
    )


def _sanitize_menu_options(options: tuple[MenuOption, ...]) -> tuple[MenuOption, ...]:
    """清理菜单选项集合。"""
    return tuple(_sanitize_menu_option(option) for option in options)


if __name__ == '__main__':
    pass
