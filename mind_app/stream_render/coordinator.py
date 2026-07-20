# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from rich.console import Console
from rich.text import Text
from mind_core.design import Design
from mind_app.stream_render.animation import AnimDriver
from mind_app.stream_state.status import (
    StatusFamily,
    StatusState
)
from mind_app.stream_render.live import TextRenderer
from mind_app.stream_state.text import TextState


class RenderCoord(object):
    """用单一 typewriter live 合成正文与轻量状态动画。"""

    def __init__(
        self,
        *,
        console: Console | None = None,
        refresh_per_second: int = 16,
    ) -> None:
        self.refresh_per_second = max(1, int(refresh_per_second))

        self.console = console or Console()

        self.text_state   = TextState(width_provider=lambda: self.console.width)
        self.status_state = StatusState()

        self.reserve_status_slot: bool = False

        self.status_driver = AnimDriver(
            is_active=lambda: self.status_state.animating,
            get_interval=self.status_state.interval,
            get_phase_rate=self.status_state.phase_rate,
            on_tick=self._on_status_tick
        )
        self.text_renderer = TextRenderer(
            console=self.console,
            refresh_per_second=self.refresh_per_second,
        )
        self.render_lock: asyncio.Lock = asyncio.Lock()

    async def stop(self, *, blink: bool = True) -> None:
        self.release_status_slot()
        self.status_state.reset()
        await self.status_driver.stop(reset_phase=True)

        async with self.render_lock:
            if self.text_state.display_text:
                final_renderable = self.text_state.final_renderable()
                await self.text_renderer.show(
                    self.text_state.display_text,
                    animate=False,
                    refresh_per_second=self.refresh_per_second
                )
                await self.text_renderer.stop(
                    blink=blink,
                    final_renderable=final_renderable
                )
                return None

            await self.text_renderer.stop(blink=blink)

    async def append(
        self,
        chunk: typing.Optional[str],
        *,
        echo: bool = True,
        display: str = TextState.STREAM,
        display_chunk: typing.Optional[str] = None,
        raw_chunk: typing.Optional[str] = None,
        display_style: typing.Optional[str] = None,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None,
        preserve_display_parts: bool = False
    ) -> None:
        animate = self.text_state.append(
            chunk,
            echo=echo,
            display=display,
            display_chunk=display_chunk,
            raw_chunk=raw_chunk,
            display_style=display_style,
            display_parts=display_parts,
            preserve_display_parts=preserve_display_parts
        )
        if not (echo and chunk):
            return None

        async with self.render_lock:
            await self._render_current(animate=animate and not self.status_state.active)

    async def set_status(
        self,
        text: typing.Optional[str],
        *,
        family: StatusFamily = StatusState.FAMILY_TOOL,
        animated: bool = True,
        reset_phase_on_text_change: bool = True
    ) -> None:
        reset_phase = self.status_state.set_status(
            text,
            family=family,
            animated=animated,
            reset_phase_on_text_change=reset_phase_on_text_change
        )

        if not self.status_state.animating:
            await self.status_driver.stop(reset_phase=True)
            await self._on_status_update()
            return None

        await self.status_driver.start(reset_phase=reset_phase and self.status_state.active)
        await self._on_status_update()

    async def clear_status(self, *, immediate: bool = False) -> None:
        had_status = self.status_state.visible
        if immediate:
            self.status_state.reset()
            await self.status_driver.stop(reset_phase=True)
            if had_status:
                await self._on_status_update()
            return None

        had_status = self.status_state.clear_status()
        if self.status_state.animating:
            await self.status_driver.start(reset_phase=False)
        else:
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
        self.status_state.set_phase(phase)
        await self._on_status_update()

    async def _on_status_update(self) -> None:
        async with self.render_lock:
            await self._render_current(animate=False)

    async def _render_current(self, *, animate: bool) -> None:
        if self.status_state.visible or self.reserve_status_slot:
            await self.text_renderer.show(
                self.text_state.display_text,
                animate=False,
                renderable=self._compose_status_renderable(),
                refresh_per_second=self.status_state.refresh_per_second()
            )
            return None

        if self.text_state.display_text:
            renderable = None
            if self.text_state.has_styles():
                tail_text = self.text_renderer.tail_text(self.text_state.display_text)
                renderable = self.text_state.renderable_for_text(tail_text)
            await self.text_renderer.show(
                self.text_state.display_text,
                animate=animate,
                renderable=renderable,
                refresh_per_second=self.refresh_per_second
            )
            return None

        await self.text_renderer.suspend()

    def _compose_status_renderable(self) -> Text:
        if self.text_state.display_text:
            base_text = self.text_renderer.tail_text(
                self.text_state.display_text,
                reserve_lines=1
            )
            if self.text_state.has_styles():
                out = self.text_state.renderable_for_text(base_text)
            else:
                out = Text(base_text)
        else:
            base_text = ""
            out = Text()

        spacer = self._status_spacer(base_text)
        if spacer:
            out.append(spacer, style="bold")

        if self.status_state.visible:
            out += Design.status_line_renderable(self.status_state.renderable())
        else:
            out += Design.status_line_renderable(Text(" ", style="bold"))

        return out

    @staticmethod
    def _status_spacer(text: str) -> str:
        if not text:
            return ""
        wants_soft_gap = RenderCoord._needs_soft_gap(text)
        if text.endswith("\n"):
            return "\n" if wants_soft_gap else ""
        return "\n\n" if wants_soft_gap else "\n"

    @staticmethod
    def _needs_soft_gap(text: str) -> bool:
        stripped = text.rstrip("\n")
        if not stripped:
            return False

        lines = stripped.splitlines()
        if not lines:
            return False

        last_line = next((line.strip() for line in reversed(lines) if line.strip()), "")
        if not last_line:
            return False

        if last_line.startswith("Sources:"):
            return True

        if "http://" in last_line or "https://" in last_line:
            return True

        return len(last_line) >= 72 and last_line[:2].isdigit() and ". " in last_line


if __name__ == '__main__':
    pass
