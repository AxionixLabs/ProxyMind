# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import replace
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from mind_app.presentation.terminal_text import sanitize_terminal_line
from ..contracts.menu import (
    MenuEmptyAcceptAction,
    MenuOption,
    MenuRequest
)
from ..contracts.views import (
    ViewCompletion,
    ViewIdentity
)
from ..rendering.menu.sanitize import (
    sanitize_menu_request as _sanitize_menu_request
)
from ..rendering.menu.selection import (
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
    visible_window
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
from ..rendering.menu.measure import line_count
from ..rendering.menu.layout import MENU_SURFACE_HORIZONTAL_INSET
from .view import BottomPaneViewStack

TUI_MENU_STYLE = Style.from_dict({
    "tui-menu.title": "bold",
    "tui-menu.title.current": "ansicyan",
    "tui-menu.status": "dim",
    "tui-menu.status.current": "bold ansicyan",
    "tui-menu.surface": "",
    "tui-menu.help": "dim",
    "tui-menu.search": "",
    "tui-menu.search.placeholder": "dim",
    "tui-menu.search.empty": "dim italic",
    "tui-menu.tab": "dim",
    "tui-menu.tab-selected": "bold ansicyan",
    "tui-menu.index": "",
    "tui-menu.index.active": "bold ansicyan",
    "tui-menu.label": "",
    "tui-menu.label.active": "bold ansicyan",
    "tui-menu.detail": "dim",
    "tui-menu.body": "",
    "tui-menu.body.empty": "dim italic",
    "tui-menu.body.heading": "bold",
    "tui-menu.review": "ansiyellow",
    "tui-menu.review-selected": "bold ansiyellow",
    "tui-menu.detail-selected": "bold ansicyan",
    "tui-menu.warning": "ansired",
    "tui-menu.error": "ansired",
    "tui-menu.label.disabled": "dim",
    "tui-menu.detail.disabled": "dim",
    "tui-menu.index.disabled": "dim",
    "tui-menu.footer": "dim",
    "tui-menu.footer.note": "dim",
    "tui-menu.footer.hint": "dim",
    "tui-menu.footer.right": "dim",
    "tui-menu.footer.right.current": "bold nodim ansicyan",
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
    SURFACE_VERTICAL_INSET: typing.Final[int]   = 1

    MIN_LABEL_WIDTH: typing.Final[int]    = _RENDER_CONFIG.min_label_width
    MIN_DETAIL_WIDTH: typing.Final[int]   = _RENDER_CONFIG.min_detail_width
    MAX_DETAIL_RESERVE: typing.Final[int] = _RENDER_CONFIG.max_detail_reserve

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
        request = request_for_tab(request)
        future = asyncio.get_running_loop().create_future()
        if not request.options and not request.body:
            future.set_result(None)
            return future

        selected = initial_selection(request.options, request.selected)
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
        self._view_stack.push(MenuView(self, state))
        return future

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
        self.invalidate()

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
        )

    def footer_fragments_for_state(
        self,
        state: MenuState,
        *,
        width: int | None = None
    ) -> StyleAndTextTuples:
        """生成指定菜单状态的透明页脚片段。"""
        return render_footer_fragments(
            state,
            width=self.get_width() if width is None else width,
            inset=self._RENDER_CONFIG.horizontal_inset,
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
        footer = render_footer_fragments(
            state,
            width=width,
            inset=self._RENDER_CONFIG.horizontal_inset,
        )
        return line_count(footer)

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
        request: MenuRequest
    ) -> None:
        """在不改变 view 对象的情况下替换其请求内容。"""
        previous_value    = selected_value(state)
        previous_selected = state.selected
        request           = self._with_generation(_sanitize_menu_request(request))

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
            self._update_query(delete_previous_query_word(state.query))
        elif key in (Keys.BracketedPaste,) and state.request.searchable:
            self.handle_paste(data, state)
        elif key in (Keys.Escape, "escape") and state.request.allow_cancel:
            self.cancel()
        elif key in (Keys.ControlC, "c-c"):
            return self.on_ctrl_c(state)
        elif key in (Keys.Enter, "enter"):
            indices = filtered_indices(state)
            if has_selectable(state.request.options, indices):
                self._choose_index(state.selected)
            elif state.request.empty_accept_action is MenuEmptyAcceptAction.CANCEL:
                self.cancel()
        elif key in ("space", " "):
            if state.request.on_space is not None:
                state.request.on_space()
            else:
                return False
        elif data == "t" and state.request.on_t is not None:
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
        state.result     = value

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
        self.invalidate()

    def _update_query(self, query: str) -> None:
        """更新搜索查询并把选择定位到新的过滤结果。"""
        state = self.state
        if state is None or not state.request.searchable:
            return None
        state.query = query
        self._normalize_filtered_state(state)
        self.invalidate()

    @staticmethod
    def _normalize_filtered_state(state: MenuState) -> None:
        """在刷新或查询变化后把选中项限制在过滤结果内。"""
        state.selected = normalized_filtered_selection(state)

    def _visible_indices(
        self,
        state: MenuState,
    ) -> tuple[int, tuple[int, ...]]:
        """返回当前查询下可见窗口对应的原始索引。"""
        return visible_window(state, visible_rows=self.VISIBLE_ROWS)

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

        @bindings.add("enter")
        def _(_event) -> None:
            state = self.state
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

        @bindings.add("space")
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.on_space is not None:
                state.request.on_space()
            elif state is not None and has_selectable(
                state.request.options,
                filtered_indices(state),
            ):
                # 非 dismiss 选项可复用空格执行 toggle；普通菜单仍保持原语义。
                option = state.request.options[state.selected]
                if not option.dismiss_on_select:
                    self._choose_index(state.selected)

        @bindings.add("t")
        def _(_event) -> None:
            state = self.state
            if state is not None and state.request.on_t is not None:
                state.request.on_t()

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
                    delete_previous_query_word(state.query),
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
