# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.console import Console
from mind_core.live_session import TypewriterStreamSession


class TextRenderer(object):
    """统一渲染正文和轻量状态。"""

    def __init__(
        self,
        *,
        console: Console | None = None,
        refresh_per_second: int = 16,
    ) -> None:
        self.default_refresh_per_second = max(1, int(refresh_per_second))
        self.session = TypewriterStreamSession(
            console=console,
            refresh_per_second=self.default_refresh_per_second,
        )

    async def show(
        self,
        content: str,
        *,
        animate: bool = False,
        renderable: typing.Optional[typing.Any] = None,
        refresh_per_second: typing.Optional[int] = None
    ) -> None:
        rate = (
            self.default_refresh_per_second
            if refresh_per_second is None
            else max(1, int(refresh_per_second))
        )
        self.session.set_refresh_per_second(rate)
        await self.session.start()
        await self.session.sync(content, animate=animate, renderable=renderable)

    async def suspend(self, *, clear: bool = False) -> None:
        await self.session.suspend(clear=clear)

    async def stop(
        self,
        *,
        blink: bool = True,
        final_renderable: typing.Optional[typing.Any] = None
    ) -> None:
        await self.session.stop(blink=blink, final_renderable=final_renderable)

    def tail_text(self, content: str, *, reserve_lines: int = 0) -> str:
        return self.session.tail_text(content, reserve_lines=reserve_lines)


if __name__ == '__main__':
    pass
