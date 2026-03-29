# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.live_session import TypewriterStreamSession


class TextRenderer(object):
    """单一正文 live renderer，同时承载正文和轻量状态 renderable。"""

    def __init__(self, *, refresh_per_second: int = 16) -> None:
        self.default_refresh_per_second = max(1, int(refresh_per_second))
        self.session = TypewriterStreamSession(refresh_per_second=self.default_refresh_per_second)

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

    async def suspend(self) -> None:
        await self.session.suspend()

    async def stop(self, *, blink: bool = True) -> None:
        await self.session.stop(blink=blink)

    def tail_text(self, content: str, *, reserve_lines: int = 0) -> str:
        return self.session.tail_text(content, reserve_lines=reserve_lines)


if __name__ == '__main__':
    pass
