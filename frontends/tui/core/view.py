# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings

from frontends.tui.contracts.views import (
    ViewCompletion,
    ViewIdentity,
)


class BottomPaneView(typing.Protocol):
    """描述可接入底部面板的交互视图最小能力。"""

    @property
    def key_bindings(self) -> KeyBindings: ...

    def fragments(self) -> StyleAndTextTuples: ...

    def footer_fragments(self) -> StyleAndTextTuples: ...

    def desired_height(self, width: int) -> int: ...

    def footer_height(self, width: int) -> int: ...

    def view_id(self) -> str | None: ...

    def generation(self) -> int: ...

    def session_id(self) -> int | None: ...

    def identity(self) -> ViewIdentity: ...

    def selected_index(self) -> int | None: ...

    def active_tab_id(self) -> str | None: ...

    def surface_style(self) -> str: ...

    def handle_key_event(self, _event: typing.Any) -> bool: ...

    def on_ctrl_c(self) -> bool: ...

    def handle_paste(self, text: str) -> bool: ...

    def is_complete(self) -> bool: ...

    def completion(self) -> ViewCompletion | None: ...

    def result(self) -> typing.Any: ...

    def dismiss_after_child_accept(self) -> bool: ...

    def clear_dismiss_after_child_accept(self) -> None: ...


class BottomPaneViewStack(object):
    """保存选择视图对象并在栈深变化时通知宿主。"""

    def __init__(
        self,
        *,
        changed: typing.Callable[[bool], None]
    ) -> None:
        self._changed = changed
        self._views: list[BottomPaneView] = []

    def __bool__(self) -> bool:
        return bool(self._views)

    def __len__(self) -> int:
        return len(self._views)

    @property
    def active_view(self) -> BottomPaneView | None:
        """返回当前栈顶视图。"""
        return self._views[-1] if self._views else None

    @property
    def views(self) -> tuple[BottomPaneView, ...]:
        """返回当前视图栈的只读快照。"""
        return tuple(self._views)

    def active_view_id(self) -> str | None:
        """返回栈顶视图的稳定标识。"""
        view = self.active_view
        return view.view_id() if view is not None else None

    def find(self, view_id: str, *, session_id: int | None = None) -> BottomPaneView | None:
        """返回当前会话中最靠近栈顶的指定身份视图。"""
        for view in reversed(self._views):
            if view.view_id() != view_id:
                continue
            if session_id is not None and view.session_id() != session_id:
                continue
            return view
        return None

    def push(self, view: BottomPaneView) -> None:
        """压入视图并通知宿主更新表面。"""
        self._views.append(view)
        self._changed(True)

    def pop(self, expected: BottomPaneView) -> bool:
        """仅在对象仍位于栈顶时弹出视图。"""
        if self.active_view is not expected:
            return False
        self._views.pop()
        self._changed(bool(self._views))
        return True

    def clear(self) -> None:
        """清空所有视图并通知宿主恢复主输入。"""
        if not self._views:
            return None
        self._views.clear()
        self._changed(False)


if __name__ == '__main__':
    pass
