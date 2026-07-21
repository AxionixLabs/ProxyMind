# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

BottomSurface: typing.TypeAlias = typing.Literal[
    "approval",
    "menu",
    "process_viewer"
]


class TuiBottomPane(object):
    """管理底部临时交互表面的层级、可见性和焦点恢复。"""

    def __init__(
        self,
        *,
        focus_surface: typing.Callable[[BottomSurface], None],
        focus_input: typing.Callable[[], None],
        invalidate: typing.Callable[[], None],
    ) -> None:
        self._focus_surface = focus_surface
        self._focus_input = focus_input
        self._invalidate = invalidate
        self._stack: list[BottomSurface] = []

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
        if not self._stack:
            return None
        self._stack.clear()
        self._focus_input()
        self._invalidate()


if __name__ == '__main__':
    pass
