# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from dataclasses import dataclass

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings

from frontends.tui.contracts.menu import MenuRequest
from frontends.tui.contracts.views import (
    ViewCompletion,
    ViewIdentity
)


class MenuViewOwner(typing.Protocol):
    """定义菜单 view 所需的最小协调器能力。"""

    key_bindings: KeyBindings

    def surface_fragments_for_state(self, state: typing.Any) -> StyleAndTextTuples: ...

    def footer_fragments_for_state(
        self,
        state: typing.Any,
        *,
        width: int | None = None,
    ) -> StyleAndTextTuples: ...

    def content_height_for_state(self, state: typing.Any, *, width: int) -> int: ...

    def footer_height_for_state(self, state: typing.Any, *, width: int) -> int: ...

    def selected_index(self, state: typing.Any) -> int | None: ...

    def handle_key_event(self, event: typing.Any) -> bool: ...

    def on_ctrl_c(self, state: typing.Any) -> bool: ...

    def handle_paste(self, text: str, state: typing.Any) -> bool: ...

    def result(self, state: typing.Any) -> typing.Any: ...

    @staticmethod
    def state_dismisses_after_child_accept(state: typing.Any) -> bool: ...

    @staticmethod
    def clear_state_child_dismissal(state: typing.Any) -> None: ...


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
class MenuView(object):
    """把单个菜单状态接入底部面板 view 契约。"""
    owner: MenuViewOwner
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

    def surface_style(self) -> str:
        """返回当前菜单 surface 使用的窗口样式。"""
        return self.state.request.surface_style

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


if __name__ == '__main__':
    pass
