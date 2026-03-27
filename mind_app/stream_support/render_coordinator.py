# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from rich.text import Text
from .animation_driver import AnimationDriver
from .status_session import StatusKind, StatusSession
from .text_renderer import TextLiveRenderer
from .text_session import TextStreamSession


class RenderCoordinator(object):
    """用单一 typewriter live 合成正文与轻量状态动画。"""

    def __init__(self, *, refresh_per_second: int = 16) -> None:
        self.refresh_per_second = max(1, int(refresh_per_second))

        self.text_session   = TextStreamSession()
        self.status_session = StatusSession()

        self.reserve_status_slot = False

        self.status_driver = AnimationDriver(
            is_active=lambda: self.status_session.active,
            get_interval=self.status_session.interval,
            get_step=self.status_session.step,
            on_tick=self._on_status_tick
        )
        self.text_renderer = TextLiveRenderer(refresh_per_second=self.refresh_per_second)
        self.render_lock: asyncio.Lock = asyncio.Lock()

    async def stop(self) -> None:
        await self.clear_status()
        self.release_status_slot()

        async with self.render_lock:
            if self.text_session.display_text:
                await self.text_renderer.show(
                    self.text_session.display_text,
                    animate=False,
                    refresh_per_second=self.refresh_per_second
                )
                await self.text_renderer.stop()
                return None

            await self.text_renderer.stop()

    async def append(
        self,
        chunk: typing.Optional[str],
        *,
        echo: bool = True,
        display: str = TextStreamSession.STREAM,
        display_chunk: typing.Optional[str] = None
    ) -> None:
        animate = self.text_session.append(
            chunk, echo=echo, display=display, display_chunk=display_chunk
        )
        if not (echo and chunk):
            return None

        async with self.render_lock:
            await self._render_current(animate=animate and not self.status_session.active)

    async def set_status(
        self,
        text: typing.Optional[str],
        *,
        kind: StatusKind = StatusSession.STATUS_SEARCH,
        animated: bool = True
    ) -> None:
        reset_phase = self.status_session.set_status(text, kind=kind, animated=animated)

        if not self.status_session.active:
            await self.status_driver.stop(reset_phase=True)
            await self._on_status_update()
            return None

        if self.status_session.animated:
            await self.status_driver.start(reset_phase=reset_phase)
        else:
            await self.status_driver.stop(reset_phase=reset_phase)
        await self._on_status_update()

    async def clear_status(self) -> None:
        had_status = self.status_session.clear_status()
        await self.status_driver.stop(reset_phase=True)
        if had_status:
            await self._on_status_update()

    async def settle_stream(self) -> None:
        async with self.render_lock:
            await self._render_current(animate=False)

    def hold_status_slot(self) -> None:
        self.reserve_status_slot = True

    def release_status_slot(self) -> None:
        self.reserve_status_slot = False

    async def _on_status_tick(self, phase: float) -> None:
        self.status_session.set_phase(phase)
        await self._on_status_update()

    async def _on_status_update(self) -> None:
        async with self.render_lock:
            await self._render_current(animate=False)

    async def _render_current(self, *, animate: bool) -> None:
        if self.status_session.active or self.reserve_status_slot:
            await self.text_renderer.show(
                self.text_session.display_text,
                animate=False,
                renderable=self._compose_status_renderable(),
                refresh_per_second=self.status_session.refresh_per_second()
            )
            return None

        if self.text_session.display_text:
            await self.text_renderer.show(
                self.text_session.display_text,
                animate=animate,
                refresh_per_second=self.refresh_per_second
            )
            return None

        await self.text_renderer.suspend()

    def _compose_status_renderable(self) -> Text:
        if self.text_session.display_text:
            out = self.text_session.renderable()
        else:
            out = Text()

        spacer = self.text_session.status_spacer()
        if spacer:
            out.append(spacer, style="bold")

        if self.status_session.active:
            out += self.status_session.renderable()
        else:
            out += Text(" ", style="bold")

        return out


if __name__ == '__main__':
    pass
