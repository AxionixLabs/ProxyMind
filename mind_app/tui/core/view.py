# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from .models import ViewCompletion


class BottomPaneView(typing.Protocol):
    """描述可接入底部面板的交互视图最小能力。"""

    @property
    def key_bindings(self) -> KeyBindings: ...

    def fragments(self) -> StyleAndTextTuples: ...

    def desired_height(self, width: int) -> int: ...

    def view_id(self) -> str | None: ...

    def generation(self) -> int: ...

    def handle_key_event(self, event: typing.Any) -> bool: ...

    def is_complete(self) -> bool: ...

    def completion(self) -> ViewCompletion | None: ...

    def result(self) -> typing.Any: ...


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
