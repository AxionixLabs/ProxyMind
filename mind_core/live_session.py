# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import random
import typing
from collections import deque
from rich.console import Console
from rich.live import Live
from rich.text import Text
from mind_core.design import Design


class LiveRenderSession(object):

    def __init__(
        self,
        refresh_per_second: int = 12,
        *,
        console: Console | None = None,
    ) -> None:
        self.console  = console or Console()
        self.out: str = ""

        self.renderable: typing.Optional[typing.Any] = None

        self.refresh_per_second: int = max(1, int(refresh_per_second))

        self.live: typing.Optional[Live] = None

    def set_refresh_per_second(self, refresh_per_second: int) -> None:
        """设置当前 Live 会话的刷新频率。"""
        rate = max(1, int(refresh_per_second))
        self.refresh_per_second = rate
        if self.live is not None:
            self.live.refresh_per_second = rate

    def _live_renderable(self) -> typing.Any:
        """返回当前 Live 使用的可渲染对象。"""
        return self.renderable if self.renderable is not None else Text(self.out)

    async def start(self) -> None:
        """启动 Live 渲染会话。"""
        if self.live:
            return None

        self.live = Live(
            self._live_renderable(),
            console=self.console,
            refresh_per_second=self.refresh_per_second,
            transient=True,
            vertical_overflow="crop"
        )
        self.live.__enter__()

    async def suspend(self, *, clear: bool = False) -> None:
        """暂停并释放当前 Live 会话。"""
        if self.live is not None:
            self.live.__exit__(None, None, None)
            self.live = None
        if clear:
            self.out = ""
            self.renderable = None

    async def stop(self) -> None:
        """停止 Live 会话并清理渲染状态。"""
        await self.suspend()
        self.renderable = None

    async def render(
        self,
        content: str,
        *,
        renderable: typing.Optional[typing.Any] = None
    ) -> None:
        """用指定内容更新 Live 画面。"""
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
        """同步内容到 Live，会话基类不处理动画参数。"""
        del animate
        await self.render(content, renderable=renderable)


class TypewriterStreamSession(LiveRenderSession):
    """管理流式正文的打字机窗口和光标渲染。"""

    MIN_VIEW_LINES = 8
    MAX_VIEW_LINES = 32
    VIEW_MARGIN    = 6

    def __init__(
        self,
        max_lines: int = MAX_VIEW_LINES,
        refresh_per_second: int = 12,
        *,
        console: Console | None = None,
    ) -> None:
        """初始化打字机窗口状态。"""
        super().__init__(refresh_per_second=refresh_per_second, console=console)

        self.lines: deque = deque(maxlen=max_lines)
        self.col: int     = 0
        self.delay: float = 0.01
        self.cursor: str  = random.choice(["█", "▉", "▋"])

    def _viewport_lines(self) -> int:
        """根据当前终端高度计算正文可见行数。"""
        height   = max(0, int(getattr(self.console, "height", 0) or 0))
        line_cap = min(int(self.lines.maxlen), self.MAX_VIEW_LINES)

        if height <= 0:
            return line_cap
        return max(self.MIN_VIEW_LINES, min(line_cap, height - self.VIEW_MARGIN))

    def _tail_text(self, text: str, *, reserve_lines: int = 0) -> str:
        """截取适合当前打字机窗口显示的尾部文本。"""
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
        """返回按窗口高度裁剪后的尾部文本。"""
        return self._tail_text(text, reserve_lines=reserve_lines)

    def _live_renderable(self) -> typing.Any:
        """返回打字机窗口当前使用的可渲染对象。"""
        return self.renderable if self.renderable is not None else Text(self._tail_text(self.out))

    async def stop(
        self,
        *,
        blink: bool = True,
        final_renderable: typing.Optional[typing.Any] = None
    ) -> None:
        """停止打字机 Live，并在结束后输出最终文本。"""
        if self.live is not None:
            try:
                if blink:
                    await Design.cursor_blink(
                        self.live, self.out, self.cursor, max_lines=self._viewport_lines()
                    )
            finally:
                self.live.__exit__(None, None, None)
                self.live = None

        final_text = self.out.rstrip("\n")
        if final_text:
            if final_renderable is not None:
                if isinstance(final_renderable, Text):
                    final_renderable = final_renderable.copy()
                    final_renderable.rstrip()
                self.console.print(final_renderable)
            else:
                self.console.print(Text(final_text))
            self.console.print()
        self.renderable = None

    async def feed(self, delta: str) -> None:
        """把新增文本以打字机动画追加到 Live。"""
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
        """直接更新打字机 Live 的完整内容。"""
        await super().render(content, renderable=renderable)

    async def sync(
        self,
        content: str,
        *,
        animate: bool = False,
        renderable: typing.Optional[typing.Any] = None
    ) -> None:
        """根据内容变化选择动画追加或直接同步。"""
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
