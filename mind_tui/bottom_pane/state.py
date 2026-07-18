# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)

BottomPaneViewKind = typing.Literal[
    "approval",
    "selection",
    "request_user_input",
    "settings",
    "mcp_elicitation"
]


@dataclass(frozen=True, slots=True)
class BottomPaneView:
    """表示一个临时接管底部交互的视图。"""

    kind: BottomPaneViewKind
    payload: dict[str, typing.Any] = field(default_factory=dict)


class BottomPaneState:
    """按入栈顺序管理底部临时视图。"""

    def __init__(self) -> None:
        """初始化空视图栈。"""
        self._views: list[BottomPaneView] = []

    @property
    def views(self) -> tuple[BottomPaneView, ...]:
        """返回当前视图栈快照。"""
        return tuple(self._views)

    @property
    def top(self) -> BottomPaneView | None:
        """返回当前接管输入的栈顶视图。"""
        return self._views[-1] if self._views else None

    @property
    def composer_visible(self) -> bool:
        """返回普通输入区是否应当显示。"""
        return not self._views

    def push(
        self,
        kind: BottomPaneViewKind,
        payload: dict[str, typing.Any] | None = None
    ) -> BottomPaneView:
        """将临时视图压入栈顶。"""
        view = BottomPaneView(kind, dict(payload or {}))
        self._views.append(view)
        return view

    def pop(self, expected: BottomPaneViewKind | None = None) -> BottomPaneView | None:
        """移除栈顶视图并可选校验视图类型。"""
        if not self._views:
            return None
        if expected is not None and self._views[-1].kind != expected:
            raise RuntimeError(
                f"bottom pane top is {self._views[-1].kind}, expected {expected}"
            )
        return self._views.pop()

    def is_active(self, kind: BottomPaneViewKind) -> bool:
        """返回指定视图是否位于栈顶。"""
        return self.top is not None and self.top.kind == kind


if __name__ == '__main__':
    pass
