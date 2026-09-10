# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from dataclasses import replace

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from frontends.terminal.text import (
    sanitize_terminal_line,
    sanitize_terminal_text,
)
from frontends.tui.contracts.menu import (
    MenuEmptyAcceptAction,
    MenuFooterCommand,
    MenuFooterHint,
    MenuFooterValue,
    MenuOption,
    MenuRequest,
    MenuShortcutAction,
    MenuTab,
    MenuTextInputMode,
)
from frontends.tui.contracts.views import (
    ViewCompletion,
    ViewIdentity
)
from .keymap import (
    TuiEditorKeymap,
    TuiListKeymap,
    TuiRuntimeKeymap,
    bind_key_action,
    key_action_matches,
    primary_binding_label,
)
from .view import BottomPaneViewStack
from ..rendering.menu.layout import MENU_SURFACE_HORIZONTAL_INSET
from ..rendering.menu.measure import line_count
from ..rendering.menu.sanitize import sanitize_menu_request as _sanitize_menu_request
from ..rendering.menu.selection import (
    ensure_selection_visible,
    delete_previous_query_word,
    filtered_indices,
    generation_is_current,
    has_selectable,
    initial_selection,
    moved_selection,
    normalized_filtered_selection,
    option_is_disabled,
    selection_at,
    selected_value,
    selection_for_request,
    visible_options,
)
from ..rendering.menu.state import (
    MenuState,
    MenuView
)
from ..rendering.menu.surface import (
    MenuRenderConfig,
    footer_fragments as render_footer_fragments,
    menu_fragments as render_menu_fragments,
    surface_footer_fragments as render_surface_footer_fragments,
    surface_fragments as render_surface_fragments
)
from ..rendering.menu.tabs import (
    request_for_tab,
    switched_tab_request
)
from ..rendering.fragments import iter_text_unit_ranges

TUI_MENU_STYLE = Style.from_dict({
    "tui-menu.title": "bold",
    "tui-menu.title.current": "",
    "tui-menu.status": "dim",
    "tui-menu.status.current": "bold",
    "tui-menu.surface": "",
    "tui-menu.help": "dim",
    "tui-menu.search": "",
    "tui-menu.search.placeholder": "dim",
    "tui-menu.search.empty": "dim italic",
    "tui-menu.input-gutter": "",
    "tui-menu.tab": "dim",
    "tui-menu.tab-selected": "bold",
    "tui-menu.index": "",
    "tui-menu.index.active": "bold",
    "tui-menu.selection-marker": "bold nodim",
    "tui-menu.label": "",
    "tui-menu.label.active": "bold",
    "tui-menu.detail": "dim",
    "tui-menu.body": "",
    "tui-menu.body.empty": "dim italic",
    "tui-menu.body.heading": "bold",
    "tui-menu.review": "",
    "tui-menu.review-selected": "bold",
    "tui-menu.detail-selected": "bold",
    "tui-menu.warning": "",
    "tui-menu.error": "",
    "tui-menu.label.disabled": "dim",
    "tui-menu.detail.disabled": "dim",
    "tui-menu.index.disabled": "dim",
    "tui-menu.footer": "dim",
    "tui-menu.footer.note": "dim",
    "tui-menu.footer.hint": "nodim",
    "tui-menu.footer.key": "dim",
    "tui-menu.footer.secondary": "dim",
    "tui-menu.footer.right": "dim",
    "tui-menu.footer.right.current": "bold nodim",
    "tui-menu.category": "dim",
})


class TuiMenu(object):
    """管理主 TUI Application 内的无边框选择菜单。"""

    _RENDER_CONFIG: typing.Final[MenuRenderConfig] = MenuRenderConfig(
        visible_rows=8,
        horizontal_inset=MENU_SURFACE_HORIZONTAL_INSET,
        min_label_width=8,
        min_detail_width=12,
        max_detail_reserve=24,
    )
    VISIBLE_ROWS: typing.Final[int] = _RENDER_CONFIG.visible_rows
    SURFACE_HORIZONTAL_INSET: typing.Final[int] = _RENDER_CONFIG.horizontal_inset
    SURFACE_VERTICAL_INSET: typing.Final[int] = 1
    MIN_LABEL_WIDTH: typing.Final[int] = _RENDER_CONFIG.min_label_width
    MIN_DETAIL_WIDTH: typing.Final[int] = _RENDER_CONFIG.min_detail_width
    MAX_DETAIL_RESERVE: typing.Final[int] = _RENDER_CONFIG.max_detail_reserve

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_menu: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
        get_width: typing.Callable[[], int],
        get_render_height: typing.Callable[[], int] | None = None,
        view_stack: BottomPaneViewStack | None = None,
        keymap: TuiListKeymap | None = None,
        editor_keymap: TuiEditorKeymap | None = None,
    ) -> None:
        self.invalidate = invalidate
        self.focus_menu = focus_menu
        self.focus_input = focus_input
        self.get_width = get_width
        self.get_render_height = get_render_height
        self._view_stack = (
            view_stack
            if view_stack is not None
            else BottomPaneViewStack(changed=self._standalone_stack_changed)
        )
        self._generations: dict[str, int] = {}
        self._session_generation: int = 0
        self.keymap = keymap or TuiRuntimeKeymap.defaults().list
        self.editor_keymap = (
            editor_keymap or TuiRuntimeKeymap.defaults().editor
        )
        self.key_bindings = self._build_key_bindings()

    def set_keymap(
        self,
        keymap: TuiListKeymap,
        editor_keymap: TuiEditorKeymap | None = None,
    ) -> None:
        """替换菜单使用的不可变运行时按键快照。"""
        self.keymap = keymap
        if editor_keymap is not None:
            self.editor_keymap = editor_keymap
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
    def _normalize_filtered_state(state: MenuState) -> None:
        """在刷新或查询变化后把选中项限制在过滤结果内。"""
        state.selected = normalized_filtered_selection(state)

    @staticmethod
    def state_dismisses_after_child_accept(state: MenuState) -> bool:
        """返回指定菜单状态的父级关闭标记。"""
        return state.dismiss_after_child_accept

    @staticmethod
    def clear_state_child_dismissal(state: MenuState) -> None:
        """清除指定菜单状态的父级关闭标记。"""
        state.dismiss_after_child_accept = False

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
        """处理 Ctrl-C；请求回调可关闭整个菜单会话。"""
        current = state or self.state
        if (
            current is None
            or current is not self.state
            or not current.request.allow_cancel
        ):
            return False
        if current.request.on_ctrl_c is not None:
            return bool(current.request.on_ctrl_c())
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
            or not (
                current.request.searchable
                or self._has_text_input(current.request)
            )
        ):
            return False
        pasted = (
            sanitize_terminal_text(text)
            if current.request.text_input_mode is MenuTextInputMode.MULTILINE
            else sanitize_terminal_line(text)
        )
        if not pasted:
            return False
        if self._has_text_input(current.request):
            return self._insert_query_text(current, pasted)
        self._update_query(current.query + pasted)
        return True

    def push(self, request: MenuRequest) -> asyncio.Future[typing.Any]:
        """压入一个子菜单并返回只属于该 view 的 future。"""
        request = self._prepare_request(request)
        base_footer_hint = request.footer_hint
        request = request_for_tab(request)
        future = asyncio.get_running_loop().create_future()
        if (
            not request.options
            and not request.body
            and not request.searchable
            and request.empty_accept_action is not MenuEmptyAcceptAction.SUBMIT_QUERY
        ):
            future.set_result(None)
            return future

        selected = initial_selection(request.options, request.selected)
        session_id = self.active_session_id
        if session_id is None:
            self._session_generation += 1
            session_id = self._session_generation
        initial_query = (
            request.initial_query if self._has_text_input(request) else ""
        )
        state = MenuState(
            request=request,
            future=future,
            selected=selected,
            session_id=session_id,
            dismiss_after_child_accept=False,
            completion=None,
            result=None,
            query=initial_query,
            query_cursor=len(initial_query),
            base_footer_hint=base_footer_hint,
            scroll_top=0,
        )
        ensure_selection_visible(state, visible_rows=self.VISIBLE_ROWS)
        self._view_stack.push(MenuView(self, state))
        return future

    def fragments(self) -> StyleAndTextTuples:
        """生成当前菜单可见窗口的格式化片段。"""
        state = self.state
        if state is None:
            return []
        return render_menu_fragments(
            state,
            width=self.get_width(),
            config=self._RENDER_CONFIG,
        )

    def surface_fragments_for_state(
        self,
        state: MenuState
    ) -> StyleAndTextTuples:
        """生成指定菜单状态的表面内容片段。"""
        return render_surface_fragments(
            state,
            width=self.get_width(),
            config=self._RENDER_CONFIG,
            max_height=(
                self.get_render_height()
                if self.get_render_height is not None
                else None
            ),
        )

    def footer_fragments_for_state(
        self,
        state: MenuState,
        *,
        width: int | None = None
    ) -> StyleAndTextTuples:
        """生成指定菜单状态的透明页脚片段。"""
        inset = (
            self._RENDER_CONFIG.horizontal_inset
            if state.request.surface_horizontal_inset is None
            else max(0, int(state.request.surface_horizontal_inset))
        )
        return render_footer_fragments(
            state,
            width=self.get_width() if width is None else width,
            inset=inset,
        )

    def content_height_for_state(self, state: MenuState, *, width: int) -> int:
        """计算指定菜单状态的内容高度。"""
        return self._content_height(state, width=width)

    def footer_height_for_state(self, state: MenuState, *, width: int) -> int:
        """计算指定菜单状态的透明页脚高度。"""
        return self._footer_height(state, width=width)

    def footer_fragments(self) -> StyleAndTextTuples:
        """生成当前菜单表面下方的透明 footer 片段。"""
        state = self.state
        return (
            render_footer_fragments(
                state,
                width=self.get_width(),
                inset=self._RENDER_CONFIG.horizontal_inset,
            )
            if state is not None
            else []
        )

    def height(self) -> int:
        """返回当前菜单占用的显示行数。"""
        state = self.state
        if state is None:
            return 0
        return self._height(state, width=self.get_width())

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

    def replace_active_if_id(
        self,
        view_id: str,
        request: MenuRequest,
        *,
        session_id: int | None = None
    ) -> bool:
        """仅在栈顶标识匹配时替换菜单内容。"""
        if self.active_view_id() != view_id:
            return False
        if not generation_is_current(
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
        session_id: int | None = None
    ) -> bool:
        """替换栈中仍存在的指定菜单内容。"""
        view = self._view_stack.find(view_id, session_id=session_id)
        state = view.state if isinstance(view, MenuView) else None
        if state is None:
            return False
        if not generation_is_current(
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
        session_id: int | None = None
    ) -> int:
        """在同一会话内原子刷新多个仍存在的菜单 view。"""
        prepared: list[tuple[MenuState, MenuRequest]] = []
        seen: set[str] = set()
        for view_id, request in updates:
            if view_id in seen:
                continue
            seen.add(view_id)
            view = self._view_stack.find(view_id, session_id=session_id)
            state = view.state if isinstance(view, MenuView) else None
            if not generation_is_current(
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

    def dismiss_view_by_id(
        self,
        view_id: str,
        *,
        session_id: int | None = None
    ) -> bool:
        """按标识取消当前菜单及其上方子菜单。"""
        return bool(
            self.dismiss_views_by_id((view_id,), session_id=session_id)
        )

    def dismiss_views_by_id(
        self,
        view_ids: typing.Iterable[str],
        *,
        session_id: int | None = None
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

    def _submit_text_input(self, state: MenuState) -> bool:
        """提交当前文本输入视图中的非空值。"""
        value = state.query.strip()
        if (
            not self._has_text_input(state.request)
            or state.request.empty_accept_action
            is not MenuEmptyAcceptAction.SUBMIT_QUERY
            or not value
        ):
            return False
        result = (
            state.request.text_input_result_factory(value)
            if state.request.text_input_result_factory is not None
            else value
        )
        self._complete(state, result, ViewCompletion.ACCEPTED)
        return True

    def _replace_query(
        self,
        state: MenuState,
        query: str,
        cursor: int,
    ) -> None:
        """替换可编辑查询并把光标限制在有效边界。"""
        state.query = query
        state.query_cursor = max(0, min(int(cursor), len(query)))
        self._normalize_filtered_state(state)
        self.invalidate()

    def _insert_query_text(self, state: MenuState, text: str) -> bool:
        """在当前查询光标处插入与编辑模式一致的安全文本。"""
        value = (
            sanitize_terminal_text(text)
            if state.request.text_input_mode is MenuTextInputMode.MULTILINE
            else sanitize_terminal_line(text)
        )
        if not value:
            return False
        cursor = state.query_cursor
        self._replace_query(
            state,
            f"{state.query[:cursor]}{value}{state.query[cursor:]}",
            cursor + len(value),
        )
        return True

    @staticmethod
    def _previous_query_boundary(query: str, cursor: int) -> int:
        """返回当前光标左侧组合文本单元的起点。"""
        boundaries = tuple(iter_text_unit_ranges(query[:cursor]))
        return boundaries[-1][0] if boundaries else 0

    @staticmethod
    def _next_query_boundary(query: str, cursor: int) -> int:
        """返回当前光标右侧组合文本单元的终点。"""
        unit = next(iter_text_unit_ranges(query[cursor:]), None)
        return cursor + unit[1] if unit is not None else len(query)

    @staticmethod
    def _previous_query_word_boundary(query: str, cursor: int) -> int:
        """返回当前光标左侧单词的起点。"""
        index = max(0, min(cursor, len(query)))
        while index > 0 and query[index - 1].isspace():
            index -= 1
        while index > 0 and not query[index - 1].isspace():
            index -= 1
        return index

    @staticmethod
    def _next_query_word_boundary(query: str, cursor: int) -> int:
        """返回当前光标右侧单词之后的位置。"""
        index = max(0, min(cursor, len(query)))
        while index < len(query) and query[index].isspace():
            index += 1
        while index < len(query) and not query[index].isspace():
            index += 1
        return index

    @staticmethod
    def _line_start(query: str, cursor: int) -> int:
        """返回光标所在逻辑行的起点。"""
        return query.rfind("\n", 0, max(0, cursor)) + 1

    @staticmethod
    def _line_end(query: str, cursor: int) -> int:
        """返回光标所在逻辑行的终点。"""
        end = query.find("\n", max(0, cursor))
        return len(query) if end < 0 else end

    @classmethod
    def _vertical_query_cursor(
        cls,
        query: str,
        cursor: int,
        step: int,
    ) -> int:
        """按逻辑行保持字符列移动多行输入光标。"""
        line_start = cls._line_start(query, cursor)
        column = cursor - line_start
        if step < 0:
            if line_start == 0:
                return cursor
            target_end = line_start - 1
            target_start = cls._line_start(query, target_end)
            return min(target_start + column, target_end)
        line_end = cls._line_end(query, cursor)
        if line_end >= len(query):
            return cursor
        target_start = line_end + 1
        target_end = cls._line_end(query, target_start)
        return min(target_start + column, target_end)

    @staticmethod
    def _has_text_input(request: MenuRequest) -> bool:
        """返回请求是否声明了文本编辑状态。"""
        return request.text_input_mode is not MenuTextInputMode.NONE

    def _handle_text_input_sequence(
        self,
        state: MenuState,
        sequence: tuple[Keys | str, ...],
        *,
        key: Keys | str | None,
        data: str,
    ) -> bool | None:
        """处理文本输入视图的统一编辑与完成动作。"""
        if not self._has_text_input(state.request):
            return None
        editor = self.editor_keymap
        cursor = state.query_cursor

        if (
            key_action_matches(self.keymap.cancel, sequence)
            and state.request.allow_cancel
        ):
            self.cancel()
            return True
        if key_action_matches(self.keymap.interrupt, sequence):
            return self.on_ctrl_c(state)
        if key_action_matches(self.keymap.accept, sequence):
            return self._submit_text_input(state)
        if (
            state.request.text_input_mode is MenuTextInputMode.MULTILINE
            and key_action_matches(editor.insert_newline, sequence)
        ):
            return self._insert_query_text(state, "\n")
        if key_action_matches(editor.move_left, sequence):
            self._replace_query(
                state,
                state.query,
                self._previous_query_boundary(state.query, cursor),
            )
            return True
        if key_action_matches(editor.move_right, sequence):
            self._replace_query(
                state,
                state.query,
                self._next_query_boundary(state.query, cursor),
            )
            return True
        if (
            state.request.text_input_mode is MenuTextInputMode.MULTILINE
            and key_action_matches(editor.move_up, sequence)
        ):
            self._replace_query(
                state,
                state.query,
                self._vertical_query_cursor(state.query, cursor, -1),
            )
            return True
        if (
            state.request.text_input_mode is MenuTextInputMode.MULTILINE
            and key_action_matches(editor.move_down, sequence)
        ):
            self._replace_query(
                state,
                state.query,
                self._vertical_query_cursor(state.query, cursor, 1),
            )
            return True
        if key_action_matches(editor.move_line_start, sequence):
            self._replace_query(
                state,
                state.query,
                self._line_start(state.query, cursor),
            )
            return True
        if key_action_matches(editor.move_line_end, sequence):
            self._replace_query(
                state,
                state.query,
                self._line_end(state.query, cursor),
            )
            return True
        if key_action_matches(editor.move_word_left, sequence):
            self._replace_query(
                state,
                state.query,
                self._previous_query_word_boundary(state.query, cursor),
            )
            return True
        if key_action_matches(editor.move_word_right, sequence):
            self._replace_query(
                state,
                state.query,
                self._next_query_word_boundary(state.query, cursor),
            )
            return True
        if key_action_matches(editor.delete_backward, sequence):
            start = self._previous_query_boundary(state.query, cursor)
            if start != cursor:
                self._replace_query(
                    state,
                    f"{state.query[:start]}{state.query[cursor:]}",
                    start,
                )
            return True
        if key_action_matches(editor.delete_forward, sequence):
            end = self._next_query_boundary(state.query, cursor)
            if end != cursor:
                self._replace_query(
                    state,
                    f"{state.query[:cursor]}{state.query[end:]}",
                    cursor,
                )
            return True
        if key_action_matches(editor.delete_line, sequence):
            start = self._line_start(state.query, cursor)
            self._replace_query(
                state,
                f"{state.query[:start]}{state.query[cursor:]}",
                start,
            )
            return True
        if key_action_matches(editor.delete_to_line_end, sequence):
            end = self._line_end(state.query, cursor)
            self._replace_query(
                state,
                f"{state.query[:cursor]}{state.query[end:]}",
                cursor,
            )
            return True
        if key_action_matches(editor.delete_word_backward, sequence):
            start = self._previous_query_word_boundary(state.query, cursor)
            self._replace_query(
                state,
                f"{state.query[:start]}{state.query[cursor:]}",
                start,
            )
            return True
        if key_action_matches(editor.delete_word_forward, sequence):
            end = self._next_query_word_boundary(state.query, cursor)
            self._replace_query(
                state,
                f"{state.query[:cursor]}{state.query[end:]}",
                cursor,
            )
            return True
        if key in (Keys.BracketedPaste,):
            return self.handle_paste(data, state)
        if (
            len(sequence) == 1
            and data
            and data.isprintable()
        ):
            return self._insert_query_text(state, data)
        return False

    def handle_key_event(self, event: typing.Any) -> bool:
        """处理统一 view 协议传入的单个按键事件。"""
        state = self.state
        if state is None:
            return False

        key_sequence = tuple(
            getattr(event, "key_sequence", ()) or ()
        )
        last_key_press = next(reversed(key_sequence), None)
        key = getattr(
            last_key_press,
            "key",
            getattr(event, "key", None),
        )
        data = getattr(event, "data", "")
        normalized_sequence = tuple(
            getattr(key_press, "key", key_press)
            for key_press in key_sequence
        ) or ((data,) if data else (key,))

        text_input_result = self._handle_text_input_sequence(
            state,
            normalized_sequence,
            key=key,
            data=data,
        )
        if text_input_result is not None:
            return text_input_result

        if (
            key_action_matches(self.keymap.move_down, normalized_sequence)
            and not (state.request.searchable and data == "j")
        ):
            self._move(1)
        elif (
            key_action_matches(self.keymap.move_up, normalized_sequence)
            and not (state.request.searchable and data == "k")
        ):
            self._move(-1)
        elif key_action_matches(self.keymap.page_down, normalized_sequence):
            self._move(self.VISIBLE_ROWS)
        elif key_action_matches(self.keymap.page_up, normalized_sequence):
            self._move(-self.VISIBLE_ROWS)
        elif key_action_matches(self.keymap.jump_top, normalized_sequence):
            self._set_selection(0, direction=1)
        elif key_action_matches(self.keymap.jump_bottom, normalized_sequence):
            self._set_selection(
                len(state.request.options) - 1,
                direction=-1,
            )
        elif key_action_matches(self.keymap.move_right, normalized_sequence):
            self._switch_tab(1)
        elif key_action_matches(self.keymap.move_left, normalized_sequence):
            self._switch_tab(-1)
        elif (
            key_action_matches(
                self.keymap.delete_query_character,
                normalized_sequence,
            )
            and state.request.searchable
        ):
            self._update_query(state.query[:-1])
        elif (
            key_action_matches(self.keymap.clear_query, normalized_sequence)
            and state.request.searchable
        ):
            self._update_query("")
        elif (
            key_action_matches(
                self.keymap.delete_query_word,
                normalized_sequence,
            )
            and state.request.searchable
        ):
            self._update_query(delete_previous_query_word(state.query))
        elif key in (Keys.BracketedPaste,) and state.request.searchable:
            self.handle_paste(data, state)
        elif (
            key_action_matches(self.keymap.cancel, normalized_sequence)
            and state.request.allow_cancel
        ):
            self.cancel()
        elif key_action_matches(self.keymap.interrupt, normalized_sequence):
            return self.on_ctrl_c(state)
        elif key_action_matches(self.keymap.accept, normalized_sequence):
            indices = filtered_indices(state)
            if has_selectable(state.request.options, indices):
                self._choose_index(state.selected)
            elif state.request.empty_accept_action is MenuEmptyAcceptAction.CANCEL:
                self.cancel()
        elif key_action_matches(self.keymap.toggle, normalized_sequence):
            if state.request.on_space is not None:
                state.request.on_space()
            else:
                return False
        elif (
            key_action_matches(self.keymap.alternate, normalized_sequence)
            and state.request.on_t is not None
        ):
            state.request.on_t()
        elif data and data.isprintable() and state.request.searchable:
            self._update_query(state.query + data)
        elif data.isdigit() and data != "0":
            if not state.request.show_option_gutter:
                return False
            indices = tuple(
                index
                for index in filtered_indices(state)
                if not option_is_disabled(state.request.options[index])
            )
            selected_number = int(data)
            if selected_number <= len(indices):
                self._choose_index(indices[selected_number - 1])
        else:
            return False
        return True

    def _switch_tab(self, step: int) -> None:
        """按方向切换页签并重置查询和选中项。"""
        state = self.state
        if state is None:
            return None
        request = switched_tab_request(
            state.request,
            step=step,
            base_footer_hint=state.base_footer_hint,
        )
        if request is None:
            return None
        state.request = request
        state.query = ""
        state.selected = initial_selection(request.options, request.selected)
        state.scroll_top = 0
        ensure_selection_visible(state, visible_rows=self.VISIBLE_ROWS)
        self.invalidate()

    def _height(self, state: MenuState, *, width: int) -> int:
        """返回指定菜单 frame 占用的显示行数。"""
        footer = render_surface_footer_fragments(
            state,
            width=width,
            config=self._RENDER_CONFIG,
        )
        return (
            self._content_height(state, width=width)
            + int(bool(footer))
            + line_count(footer)
        )

    def _content_height(self, state: MenuState, *, width: int) -> int:
        """返回指定菜单表面内容占用的显示行数。"""
        content = render_surface_fragments(
            state,
            width=width,
            config=self._RENDER_CONFIG,
        )
        return line_count(content)

    def _footer_height(self, state: MenuState, *, width: int) -> int:
        """返回指定菜单透明 footer 占用的显示行数。"""
        inset = (
            self._RENDER_CONFIG.horizontal_inset
            if state.request.surface_horizontal_inset is None
            else max(0, int(state.request.surface_horizontal_inset))
        )
        footer = render_footer_fragments(
            state,
            width=width,
            inset=inset,
        )
        return line_count(footer)

    def _replace_state_request(
        self,
        state: MenuState,
        request: MenuRequest
    ) -> None:
        """在不改变 view 对象的情况下替换其请求内容。"""
        previous_value = selected_value(state)
        previous_selected = state.selected
        request = self._prepare_request(request)

        state.base_footer_hint = request.footer_hint

        request = request_for_tab(
            request,
            preferred_tab_id=state.request.active_tab_id,
        )

        state.request = request

        if not request.searchable:
            state.query = ""
        if request.options:
            state.selected = selection_for_request(
                request.options,
                previous_selected,
                previous_value,
            )
        else:
            state.selected = 0
        self._normalize_filtered_state(state)
        ensure_selection_visible(state, visible_rows=self.VISIBLE_ROWS)

    def _prepare_request(self, request: MenuRequest) -> MenuRequest:
        """解析运行时快捷键标签并清理菜单展示字段。"""
        resolved = replace(
            request,
            footer_hint=self._resolve_footer_hint(request.footer_hint),
            options=tuple(
                replace(
                    option,
                    selected_footer_hint=self._resolve_footer_hint(
                        option.selected_footer_hint
                    ),
                )
                for option in request.options
            ),
            tabs=tuple(self._resolve_tab_footer(tab) for tab in request.tabs),
        )
        prepared = self._with_generation(_sanitize_menu_request(resolved))
        self._validate_text_input_keymap(prepared)
        return prepared

    def _validate_text_input_keymap(self, request: MenuRequest) -> None:
        """拒绝多行编辑器中确认与换行的非 Enter 重叠绑定。"""
        if request.text_input_mode is not MenuTextInputMode.MULTILINE:
            return None
        newline_sequences = {
            sequence
            for binding in self.editor_keymap.insert_newline
            for sequence in binding.key_sequences
        }
        for binding in self.keymap.accept:
            overlap = newline_sequences.intersection(binding.key_sequences)
            if not overlap:
                continue
            bare_enter = bool(
                len(binding.strokes) == 1
                and binding.strokes[0].key_name == "enter"
                and not binding.strokes[0].modifiers
            )
            if not bare_enter:
                raise ValueError(
                    "tui multiline accept conflicts with "
                    f"editor.insert_newline: {binding.label}"
                )

    def _resolve_tab_footer(self, tab: MenuTab) -> MenuTab:
        """解析页签及其选项的运行时快捷键标签。"""
        return replace(
            tab,
            footer_hint=(
                self._resolve_footer_hint(tab.footer_hint)
                if tab.footer_hint is not None
                else None
            ),
            options=tuple(
                replace(
                    option,
                    selected_footer_hint=self._resolve_footer_hint(
                        option.selected_footer_hint
                    ),
                )
                for option in tab.options
            ),
        )

    def _resolve_footer_hint(self, hint: MenuFooterValue) -> MenuFooterValue:
        """用当前 List Keymap 解析 footer 的按键标签。"""
        if not isinstance(hint, MenuFooterHint):
            return hint

        bindings_by_action = {
            MenuShortcutAction.ACCEPT: self.keymap.accept,
            MenuShortcutAction.CANCEL: self.keymap.cancel,
            MenuShortcutAction.TOGGLE: self.keymap.toggle,
            MenuShortcutAction.ALTERNATE: self.keymap.alternate,
        }
        commands: list[MenuFooterCommand] = []
        for command in hint.commands:
            labels: list[str] = []
            for action in command.actions:
                bindings = bindings_by_action[action]
                label = primary_binding_label(bindings).casefold()
                if label and label not in labels:
                    labels.append(label)
            if labels:
                commands.append(
                    MenuFooterCommand(
                        actions=command.actions,
                        description=command.description,
                        key_labels=tuple(labels),
                    )
                )
        if not commands:
            return ""
        return MenuFooterHint(
            commands=tuple(commands),
            prefix=hint.prefix,
            separator=hint.separator,
        )

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

    def _complete(
        self,
        state: MenuState | None,
        value: typing.Any,
        completion: ViewCompletion
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
        completion: ViewCompletion
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

    def _active_menu_view(self) -> MenuView | None:
        """返回当前栈顶属于此控制器的菜单 view。"""
        view = self._view_stack.active_view
        if isinstance(view, MenuView) and view.owner is self:
            return view
        return None

    def _menu_views(self) -> tuple[MenuView, ...]:
        """返回此控制器当前持有的菜单 view。"""
        return tuple(
            view
            for view in self._view_stack.views
            if isinstance(view, MenuView) and view.owner is self
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
        selected = moved_selection(state, step)
        if selected is None:
            return None
        state.selected = selected
        ensure_selection_visible(state, visible_rows=self.VISIBLE_ROWS)
        self.invalidate()

    def _set_selection(self, selected: int, *, direction: int = 1) -> None:
        """把当前选择定位到指定索引并跳过禁用项。"""
        state = self.state
        if state is None:
            return None
        next_selected = selection_at(
            state,
            selected,
            direction=direction,
        )
        if next_selected is None:
            return None
        state.selected = next_selected
        ensure_selection_visible(state, visible_rows=self.VISIBLE_ROWS)
        self.invalidate()

    def _update_query(self, query: str) -> None:
        """更新搜索查询并把选择定位到新的过滤结果。"""
        state = self.state
        if state is None or not (
            state.request.searchable or self._has_text_input(state.request)
        ):
            return None
        state.query = query
        state.query_cursor = len(query)
        state.scroll_top = 0
        self._normalize_filtered_state(state)
        ensure_selection_visible(state, visible_rows=self.VISIBLE_ROWS)
        self.invalidate()

    def _choose_index(self, index: int) -> None:
        """按绝对索引提交菜单选项。"""
        state = self.state
        if state is None or not (0 <= index < len(state.request.options)):
            return None
        if state.request.searchable and index not in filtered_indices(state):
            return None
        option = state.request.options[index]
        if option_is_disabled(option):
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
        return visible_options(state, visible_rows=self.VISIBLE_ROWS)

    def _build_key_bindings(self) -> KeyBindings:
        """创建内嵌菜单局部按键绑定。"""
        bindings = KeyBindings()
        text_input = Condition(
            lambda: bool(
                self.state is not None
                and self._has_text_input(self.state.request)
            )
        )
        multiline_input = Condition(
            lambda: bool(
                self.state is not None
                and self.state.request.text_input_mode
                is MenuTextInputMode.MULTILINE
            )
        )
        list_navigation = ~text_input
        non_searchable = Condition(
            lambda: bool(
                self.state is not None
                and not self.state.request.searchable
                and not self._has_text_input(self.state.request)
            )
        )

        @bind_key_action(bindings, self.keymap.accept)
        def _(_event) -> None:
            state = self.state
            if state is not None and self._submit_text_input(state):
                return None
            indices = filtered_indices(state) if state is not None else ()
            if (
                state is not None
                and has_selectable(state.request.options, indices)
            ):
                self._choose_index(state.selected)
            elif (
                state is not None
                and state.request.empty_accept_action is MenuEmptyAcceptAction.CANCEL
            ):
                self.cancel()

        @bind_key_action(bindings, self.keymap.toggle)
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                self._insert_query_text(state, " ")
            elif state is not None and state.request.on_space is not None:
                state.request.on_space()
            elif state is not None and has_selectable(
                state.request.options,
                filtered_indices(state),
            ):
                # 非 dismiss 选项可复用空格执行 toggle；普通菜单仍保持原语义。
                option = state.request.options[state.selected]
                if not option.dismiss_on_select:
                    self._choose_index(state.selected)

        @bind_key_action(
            bindings,
            self.keymap.alternate,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.on_t is not None:
                state.request.on_t()

        @bind_key_action(
            bindings,
            tuple(
                binding
                for binding in self.keymap.move_down
                if binding.keys != ("j",)
            ),
            binding_filter=list_navigation,
        )
        @bind_key_action(
            bindings,
            tuple(
                binding
                for binding in self.keymap.move_down
                if binding.keys == ("j",)
            ),
            binding_filter=non_searchable,
        )
        def _(_event) -> None:
            self._move(1)

        @bind_key_action(
            bindings,
            tuple(
                binding
                for binding in self.keymap.move_up
                if binding.keys != ("k",)
            ),
            binding_filter=list_navigation,
        )
        @bind_key_action(
            bindings,
            tuple(
                binding
                for binding in self.keymap.move_up
                if binding.keys == ("k",)
            ),
            binding_filter=non_searchable,
        )
        def _(_event) -> None:
            self._move(-1)

        @bind_key_action(
            bindings,
            self.keymap.page_down,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            self._move(self.VISIBLE_ROWS)

        @bind_key_action(
            bindings,
            self.keymap.page_up,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            self._move(-self.VISIBLE_ROWS)

        @bind_key_action(
            bindings,
            self.keymap.jump_top,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            self._set_selection(0)

        @bind_key_action(
            bindings,
            self.keymap.jump_bottom,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None:
                self._set_selection(
                    len(state.request.options) - 1,
                    direction=-1,
                )

        @bind_key_action(
            bindings,
            self.keymap.move_right,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            self._switch_tab(1)

        @bind_key_action(
            bindings,
            self.keymap.move_left,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            self._switch_tab(-1)

        @bind_key_action(
            bindings,
            self.editor_keymap.move_left,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                self._replace_query(
                    state,
                    state.query,
                    self._previous_query_boundary(
                        state.query,
                        state.query_cursor,
                    ),
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.move_right,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                self._replace_query(
                    state,
                    state.query,
                    self._next_query_boundary(
                        state.query,
                        state.query_cursor,
                    ),
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.move_up,
            binding_filter=multiline_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None:
                self._replace_query(
                    state,
                    state.query,
                    self._vertical_query_cursor(
                        state.query,
                        state.query_cursor,
                        -1,
                    ),
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.move_down,
            binding_filter=multiline_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None:
                self._replace_query(
                    state,
                    state.query,
                    self._vertical_query_cursor(
                        state.query,
                        state.query_cursor,
                        1,
                    ),
                )

        newline_bindings = tuple(
            binding
            for binding in self.editor_keymap.insert_newline
            if not (
                len(binding.strokes) == 1
                and binding.strokes[0].key_name == "enter"
                and not binding.strokes[0].modifiers
            )
        )

        @bind_key_action(
            bindings,
            newline_bindings,
            binding_filter=multiline_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None:
                self._insert_query_text(state, "\n")

        @bind_key_action(
            bindings,
            self.editor_keymap.move_line_start,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                self._replace_query(
                    state,
                    state.query,
                    self._line_start(state.query, state.query_cursor),
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.move_line_end,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                self._replace_query(
                    state,
                    state.query,
                    self._line_end(state.query, state.query_cursor),
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.move_word_left,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                self._replace_query(
                    state,
                    state.query,
                    self._previous_query_word_boundary(
                        state.query,
                        state.query_cursor,
                    ),
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.move_word_right,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                self._replace_query(
                    state,
                    state.query,
                    self._next_query_word_boundary(
                        state.query,
                        state.query_cursor,
                    ),
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.delete_backward,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                cursor = state.query_cursor
                start = self._previous_query_boundary(state.query, cursor)
                if start != cursor:
                    self._replace_query(
                        state,
                        f"{state.query[:start]}{state.query[cursor:]}",
                        start,
                    )

        @bind_key_action(
            bindings,
            self.editor_keymap.delete_forward,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                cursor = state.query_cursor
                end = self._next_query_boundary(state.query, cursor)
                if end != cursor:
                    self._replace_query(
                        state,
                        f"{state.query[:cursor]}{state.query[end:]}",
                        cursor,
                    )

        @bind_key_action(
            bindings,
            self.editor_keymap.delete_word_backward,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                cursor = state.query_cursor
                start = self._previous_query_word_boundary(
                    state.query,
                    cursor,
                )
                self._replace_query(
                    state,
                    f"{state.query[:start]}{state.query[cursor:]}",
                    start,
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.delete_word_forward,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                cursor = state.query_cursor
                end = self._next_query_word_boundary(state.query, cursor)
                self._replace_query(
                    state,
                    f"{state.query[:cursor]}{state.query[end:]}",
                    cursor,
                )

        @bind_key_action(
            bindings,
            self.editor_keymap.delete_line,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                cursor = state.query_cursor
                self._replace_query(state, state.query[cursor:], 0)

        @bind_key_action(
            bindings,
            self.editor_keymap.delete_to_line_end,
            binding_filter=text_input,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and self._has_text_input(state.request):
                cursor = state.query_cursor
                self._replace_query(state, state.query[:cursor], cursor)

        @bind_key_action(
            bindings,
            self.keymap.delete_query_character,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.searchable:
                self._update_query(state.query[:-1])

        @bind_key_action(
            bindings,
            self.keymap.clear_query,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            self._update_query("")

        @bind_key_action(
            bindings,
            self.keymap.delete_query_word,
            binding_filter=list_navigation,
        )
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.searchable:
                self._update_query(
                    delete_previous_query_word(state.query),
                )

        @bindings.add(Keys.BracketedPaste)
        def _(event) -> None:
            state = self.state
            if state is not None and (
                state.request.searchable or self._has_text_input(state.request)
            ):
                self.handle_paste(getattr(event, "data", ""), state)

        @bindings.add(Keys.Any)
        def _(event) -> None:
            state = self.state
            data = getattr(event, "data", "")
            if (
                state is not None
                and (
                    state.request.searchable
                    or self._has_text_input(state.request)
                )
                and data
                and data.isprintable()
            ):
                if self._has_text_input(state.request):
                    self._insert_query_text(state, data)
                else:
                    self._update_query(state.query + data)

        @bind_key_action(bindings, self.keymap.cancel, eager=True)
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.allow_cancel:
                self.cancel()

        @bind_key_action(bindings, self.keymap.interrupt)
        def _(_event) -> None:
            self.on_ctrl_c()

        for number in range(1, 10):
            @bindings.add(str(number))
            def _(_event, selected_number=number) -> None:
                state = self.state
                if state is None:
                    return None
                if self._has_text_input(state.request):
                    self._insert_query_text(state, str(selected_number))
                    return None
                if state.request.searchable:
                    self._update_query(state.query + str(selected_number))
                    return None
                if not state.request.show_option_gutter:
                    return None
                indices = tuple(
                    index
                    for index in filtered_indices(state)
                    if not option_is_disabled(
                        state.request.options[index],
                    )
                )
                if selected_number <= len(indices):
                    self._choose_index(indices[selected_number - 1])

        return bindings


if __name__ == '__main__':
    pass
