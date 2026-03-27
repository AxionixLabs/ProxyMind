# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from rich.text import Text
from mind_core.design import Design

StatusKind = typing.Literal["search", "tool", "wait"]


class StatusSession(object):
    """管理搜索/工具状态语义和渲染配置。"""

    STATUS_SEARCH: typing.Final[StatusKind] = "search"
    STATUS_TOOL: typing.Final[StatusKind] = "tool"
    STATUS_WAIT: typing.Final[StatusKind] = "wait"

    def __init__(
        self
    ) -> None:
        self.text: str = ""
        self.kind: StatusKind = self.STATUS_SEARCH
        self.phase: float = 0.0
        self.animated: bool = True

    @property
    def active(self) -> bool:
        return bool(self.text)

    def set_status(
        self,
        text: typing.Optional[str],
        *,
        kind: StatusKind = STATUS_SEARCH,
        animated: bool = True
    ) -> bool:
        status = str(text or "").strip()
        if not status:
            return self.clear_status()

        next_kind = kind
        next_animated = bool(animated)
        reset_phase = (
            status != self.text
            or next_kind != self.kind
            or next_animated != self.animated
        )
        if reset_phase:
            self.phase = 0.0

        self.text = status
        self.kind = next_kind
        self.animated = next_animated
        return reset_phase

    def clear_status(self) -> bool:
        was_active = self.active
        self.text = ""
        self.kind = self.STATUS_SEARCH
        self.phase = 0.0
        self.animated = True
        return was_active

    def set_phase(self, phase: float) -> None:
        self.phase = float(phase or 0.0)

    def renderable(self) -> Text:
        if not self.animated and self.kind == self.STATUS_TOOL:
            return Design.tool_status_static_renderable(self.text)
        if not self.animated:
            return Text(self.text, style="bold #8FA4B8")
        if self.kind == self.STATUS_TOOL:
            return Design.tool_status_renderable(self.phase, self.text)
        if self.kind == self.STATUS_WAIT:
            return Design.thinking_status_renderable(self.phase, self.text)
        return Design.search_status_renderable(self.phase, self.text)

    def refresh_per_second(self) -> int:
        if not self.animated:
            return 16
        if self.kind == self.STATUS_TOOL:
            return int(Design.tool_status_refresh_per_second())
        if self.kind == self.STATUS_WAIT:
            return 18
        return 24

    def interval(self) -> float:
        if not self.animated:
            return 1 / 16
        if self.kind == self.STATUS_TOOL:
            return float(Design.tool_status_interval())
        if self.kind == self.STATUS_WAIT:
            return 1 / 18
        return 1 / 24

    def step(self) -> float:
        if not self.animated:
            return 0.0
        if self.kind == self.STATUS_TOOL:
            return float(Design.tool_status_step())
        if self.kind == self.STATUS_WAIT:
            return 0.65
        return 1.0


if __name__ == '__main__':
    pass
