# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing

from rich.text import Text
from mind_core.design import Design
from .duration import format_elapsed

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
        self.started_at: float = 0.0

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
            self.started_at = time.perf_counter()

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
        self.started_at = 0.0
        return was_active

    def set_phase(self, phase: float) -> None:
        self.phase = float(phase or 0.0)

    def renderable(self) -> Text:
        if not self.animated and self.kind == self.STATUS_TOOL:
            out = Design.tool_status_static_renderable(self.text)
            self._append_elapsed(out)
            return out
        if not self.animated:
            out = Text(self.text, style="bold #8FA4B8")
            self._append_elapsed(out)
            return out
        if self.kind == self.STATUS_TOOL:
            out = Design.tool_status_renderable(self.phase, self.text)
            self._append_elapsed(out)
            return out
        if self.kind == self.STATUS_WAIT:
            out = Design.thinking_status_renderable(self.phase, self.text)
            self._append_elapsed(out)
            return out
        out = Design.search_status_renderable(self.phase, self.text)
        self._append_elapsed(out)
        return out

    def refresh_per_second(self) -> int:
        if not self.animated:
            return 4
        if self.kind == self.STATUS_TOOL:
            return int(Design.tool_status_refresh_per_second())
        if self.kind == self.STATUS_WAIT:
            return 18
        return 24

    def interval(self) -> float:
        if not self.animated:
            return 0.25
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

    def _append_elapsed(self, out: Text) -> None:
        if not self.started_at:
            return None

        elapsed = max(0.0, time.perf_counter() - self.started_at)
        out.append(" ", style="bold #5F6E76")
        out.append(format_elapsed(elapsed), style="bold #7E9198")


if __name__ == '__main__':
    pass
