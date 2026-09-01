# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .view import (
    BottomPaneView,
    BottomPaneViewStack,
    ViewIdentity,
)

BottomSurface: typing.TypeAlias = typing.Literal[
    "approval",
    "menu"
]


class TuiBottomPane(object):
    """管理底部临时交互表面的层级、可见性和焦点恢复。"""

    def __init__(
        self,
        *,
        focus_surface: typing.Callable[[BottomSurface], None],
        focus_input: typing.Callable[[], None],
        invalidate: typing.Callable[[], None]
    ) -> None:
        self._focus_surface = focus_surface
        self._focus_input = focus_input
        self._invalidate = invalidate
        self._stack: list[BottomSurface] = []
        self.view_stack = BottomPaneViewStack(
            changed=self._view_stack_changed,
        )

    @property
    def active_surface(self) -> BottomSurface | None:
        """返回当前接收输入的临时交互表面。"""
        return self._stack[-1] if self._stack else None

    @property
    def transient_active(self) -> bool:
        """返回当前是否存在临时交互表面。"""
        return bool(self._stack)

    @property
    def input_visible(self) -> bool:
        """返回主输入区当前是否可见。"""
        return not self._stack

    @property
    def active_view(self) -> BottomPaneView | None:
        """返回当前可见的对象化选择视图。"""
        if self.active_surface != "menu":
            return None
        return self.view_stack.active_view

    @property
    def active_view_identity(self) -> ViewIdentity | None:
        """返回当前选择视图的稳定身份快照。"""
        view = self.active_view
        return view.identity() if view is not None else None

    def _view_stack_changed(self, active: bool) -> None:
        """让选择视图栈与 menu 表面的生命周期保持一致。"""
        if active:
            if "menu" not in self._stack:
                self.activate("menu")
            elif self.active_surface == "menu":
                self._focus_surface("menu")
                self._invalidate()
            else:
                self._invalidate()
            return None
        self.deactivate("menu")

    def is_active(self, surface: BottomSurface) -> bool:
        """返回指定表面是否位于交互栈顶。"""
        return self.active_surface == surface

    def activate(self, surface: BottomSurface) -> None:
        """激活指定表面，并保留当前表面用于后续恢复。"""
        if surface in self._stack:
            self._stack.remove(surface)
        self._stack.append(surface)
        self._focus_surface(surface)
        self._invalidate()

    def deactivate(self, surface: BottomSurface) -> None:
        """移除指定表面，并恢复上一层表面或主输入焦点。"""
        if surface not in self._stack:
            return None
        self._stack.remove(surface)
        active = self.active_surface
        if active is None:
            self._focus_input()
        else:
            self._focus_surface(active)
        self._invalidate()

    def clear(self) -> None:
        """清空全部临时表面并恢复主输入焦点。"""
        had_views = bool(self.view_stack)
        if not self._stack and not had_views:
            return None

        self.view_stack.clear()
        self._stack.clear()
        self._focus_input()
        self._invalidate()


if __name__ == '__main__':
    pass
