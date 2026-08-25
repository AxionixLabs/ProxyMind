# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.data_structures import (
    Point,
    Size
)
from prompt_toolkit.layout.screen import Screen

BottomSurface = typing.Literal[
    "approval",
    "menu"
]


@dataclass(frozen=True, slots=True)
class InlineRendererState(object):
    """保存进入完整终端画面前的 renderer diff 状态。"""
    cursor_pos: Point
    last_screen: Screen | None
    last_size: Size | None
    last_style: str | None
    last_cursor_shape: CursorShape | None
    min_available_height: int


@dataclass(frozen=True, slots=True)
class FrameGeometry(object):
    """描述单次终端渲染使用的固定尺寸。"""
    width: int
    height: int
    revision: int


@dataclass(frozen=True, slots=True)
class ComposerLayout(object):
    """描述单帧输入表面、弹层和信息栏的高度预算。"""
    input_top_padding_height: int
    input_height: int
    input_bottom_padding_height: int
    popup_height: int
    footer_height: int

    @property
    def input_surface_height(self) -> int:
        """返回输入内容及其内部上下留白的总高度。"""
        return (
            self.input_top_padding_height
            + self.input_height
            + self.input_bottom_padding_height
        )

    @property
    def input_stack_height(self) -> int:
        """返回输入表面及其弹层或信息栏的总高度。"""
        return self.input_surface_height + self.popup_height + self.footer_height


@dataclass(frozen=True, slots=True)
class AuxiliaryPaneLayout(object):
    """描述状态、进程和排队消息的高度分配结果。"""
    status_height: int
    process_status_height: int
    queued_height: int
    interaction_gap_height: int


@dataclass(frozen=True, slots=True)
class OverlayLayout(object):
    """描述全屏 overlay 的标题、正文和 footer 高度。"""
    header_height: int
    content_height: int
    footer_height: int

    @property
    def total_height(self) -> int:
        """返回 overlay 占用的总高度。"""
        return self.header_height + self.content_height + self.footer_height


@dataclass(frozen=True, slots=True)
class ActiveViewLayout(object):
    """描述单帧临时交互表面的高度预算。"""
    surface: BottomSurface | None
    available_height: int
    top_padding_height: int
    content_height: int
    bottom_padding_height: int
    footer_height: int

    @property
    def total_height(self) -> int:
        """返回临时交互表面及其留白的总高度。"""
        return (
            self.top_padding_height
            + self.content_height
            + self.bottom_padding_height
            + self.footer_height
        )


@dataclass(frozen=True, slots=True)
class BottomPaneLayout(object):
    """描述单帧底部状态区与交互区域的统一高度预算。"""
    outer_top_inset_height: int
    status_height: int
    process_status_height: int
    queued_height: int
    interaction_gap_height: int
    composer: ComposerLayout
    active_view: ActiveViewLayout

    @property
    def interaction_height(self) -> int:
        """返回输入区或临时交互表面当前占用的高度。"""
        if self.active_view.surface is not None:
            return self.active_view.total_height
        return self.composer.input_stack_height

    @property
    def content_height(self) -> int:
        """返回不含外部顶部间距的底部面板高度。"""
        return (
            self.status_height
            + self.process_status_height
            + self.queued_height
            + self.interaction_gap_height
            + self.interaction_height
        )

    @property
    def total_height(self) -> int:
        """返回包含外部顶部间距的底部面板总高度。"""
        return self.outer_top_inset_height + self.content_height


if __name__ == '__main__':
    pass
