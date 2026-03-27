# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import random
import typing
from collections import deque
from rich.live import Live
from rich.text import Text
from mind_core.design import Design


class LiveRenderSession(object):

    def __init__(self, refresh_per_second: int = 12) -> None:
        self.out: str = ""
        self.renderable: typing.Optional[typing.Any] = None
        self.refresh_per_second: int = max(1, int(refresh_per_second))
        self.live: typing.Optional[Live] = None

    def set_refresh_per_second(self, refresh_per_second: int) -> None:
        rate = max(1, int(refresh_per_second))
        self.refresh_per_second = rate
        if self.live is not None:
            self.live.refresh_per_second = rate

    def _live_renderable(self) -> typing.Any:
        return self.renderable if self.renderable is not None else Text(self.out, style="bold")

    async def start(self) -> None:
        if self.live:
            return None

        self.live = Live(
            self._live_renderable(),
            console=Design.console,
            refresh_per_second=self.refresh_per_second,
            transient=True,
            vertical_overflow="crop"
        )
        self.live.__enter__()

    async def suspend(self) -> None:
        if self.live is None:
            return None
        self.live.__exit__(None, None, None)
        self.live = None

    async def stop(self) -> None:
        await self.suspend()
        self.renderable = None

    async def render(
        self,
        content: str,
        *,
        renderable: typing.Optional[typing.Any] = None
    ) -> None:
        if self.live is None:
            return None

        self.out = content
        self.renderable = renderable
        self.live.update(self._live_renderable())

    async def sync(
        self,
        content: str,
        *,
        animate: bool = False,
        renderable: typing.Optional[typing.Any] = None
    ) -> None:
        del animate
        await self.render(content, renderable=renderable)


class TypewriterStreamSession(LiveRenderSession):

    MIN_VIEW_LINES = 8
    VIEW_MARGIN = 4

    def __init__(self, max_lines: int = 16, refresh_per_second: int = 12) -> None:
        self.lines: deque = deque(maxlen=max_lines)
        self.col: int = 0
        self.delay: float = 0.01
        self.cursor: str = random.choice(["█", "▉", "▋"])
        super().__init__(refresh_per_second=refresh_per_second)

    def _viewport_lines(self) -> int:
        height = max(0, int(getattr(Design.console, "height", 0) or 0))
        if height <= 0:
            return self.lines.maxlen
        return max(self.MIN_VIEW_LINES, min(self.lines.maxlen, height - self.VIEW_MARGIN))

    def _tail_text(self, text: str, *, reserve_lines: int = 0) -> str:
        max_lines = self._viewport_lines() - max(0, int(reserve_lines))
        if not text or max_lines <= 0:
            return text

        parts = text.split("\n")
        if text.endswith("\n"):
            rows = parts[-max_lines - 1:]
        else:
            rows = parts[-max_lines:]
        return "\n".join(rows)

    def tail_text(self, text: str, *, reserve_lines: int = 0) -> str:
        return self._tail_text(text, reserve_lines=reserve_lines)

    def _live_renderable(self) -> typing.Any:
        return self.renderable if self.renderable is not None else Text(self._tail_text(self.out), style="bold")

    async def stop(self) -> None:
        if self.live is not None:
            try:
                await Design.cursor_blink(
                    self.live, self.out, self.cursor, max_lines=self._viewport_lines()
                )
            finally:
                self.live.__exit__(None, None, None)
                self.live = None

        if self.out:
            Design.console.print(Text(self.out, style="bold"))
        Design.console.print()
        self.renderable = None

    async def feed(self, delta: str) -> None:
        if not delta or not self.live:
            return None

        final_delay = max(0.0015, self.delay * 0.65)
        self.out, self.delay = await Design.typewriter(
            self.live,
            delta,
            self.out,
            self.delay,
            final_delay,
            self.cursor,
            max_lines=self._viewport_lines()
        )

    async def render(
        self,
        content: str,
        *,
        renderable: typing.Optional[typing.Any] = None
    ) -> None:
        await super().render(content, renderable=renderable)

    async def sync(
        self,
        content: str,
        *,
        animate: bool = False,
        renderable: typing.Optional[typing.Any] = None
    ) -> None:
        if self.live is None:
            return None

        if renderable is not None:
            return await self.render(content, renderable=renderable)

        if animate and content.startswith(self.out):
            delta = content[len(self.out):]
            if delta:
                self.renderable = None
                return await self.feed(delta)
            return None

        await self.render(content)


if __name__ == '__main__':
    pass
