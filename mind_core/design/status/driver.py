# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
import asyncio
from rich.live import Live
from rich.text import Text
from rich.console import Console
from ..utils import mix_hex_color
from .agent_frames import (
    render_agent_connect_frame,
    render_agent_wait_frame
)
from .renderers import StatusRenderer
from .types import AgentLiveTheme
from mind_nova.modes import (
    DEFAULT_RUN_MODE, RunMode
)
from mind_nova import const


class DesignStatusLiveDriver(StatusRenderer):

    design_level: str
    console: Console | None = None

    STARTUP_SWEEP_ENTRY_PAD: float     = 5.2
    STARTUP_SWEEP_EXIT_PAD: float      = 7.8
    STARTUP_SWEEP_CHARS_PER_SEC: float = 18.0
    STARTUP_SWEEP_LEAD_SPAN: float     = 4.6
    STARTUP_SWEEP_TAIL_SPAN: float     = 9.4
    STARTUP_SWEEP_PEAK_RADIUS: float   = 0.92

    @staticmethod
    def _external_mcp_item_text(item: dict[str, typing.Any]) -> str:
        name   = str(item.get("name") or "server").strip() or "server"
        state  = str(item.get("state") or "queued").strip().lower()
        detail = str(item.get("detail") or "").strip()

        if state == "failed":
            return f"{name}: {detail or 'failed'}"

        if detail:
            return f"{name} {detail}"

        if state == "ready":
            try:
                tool_count = int(item.get("tools") or 0)
            except (TypeError, ValueError, OverflowError):
                tool_count = 0
            return f"{name} {tool_count} tools"

        if state == "empty":
            return f"{name} 0 tools"
        if state == "linking":
            return f"{name} linking"
        if state == "queued":
            return f"{name} queued"

        return name

    @staticmethod
    def _external_mcp_detail_color(state: str, detail: str, colors: dict[str, str]) -> str:
        text = str(detail or "").strip().lower()

        normalized_state = str(state or "").strip().lower()
        if normalized_state == "more" or text.startswith("..."):
            return colors["detail_dim"]
        if normalized_state in {"linking", "queued"}:
            return colors["detail_wait"]
        if normalized_state == "ready":
            return colors["detail_ready"]
        if normalized_state == "empty":
            return colors["detail_warn"]
        if normalized_state == "failed":
            return colors["detail_warn"]

        return colors["detail"]

    @staticmethod
    def _external_mcp_detail_connector(index: int, count: int) -> str:
        """返回外部 MCP 详情行连接符；单条详情保持轻量，列表详情使用树形线。"""
        if count <= 1:
            return "└ "
        return "└─ " if index >= count - 1 else "├─ "

    @classmethod
    def _external_mcp_link_focus(
        cls,
        phase: float,
        span: int
    ) -> float:
        return cls._drift_focus(
            phase * cls.STARTUP_SWEEP_CHARS_PER_SEC,
            span,
            entry_pad=cls.STARTUP_SWEEP_ENTRY_PAD,
            exit_pad=cls.STARTUP_SWEEP_EXIT_PAD
        )

    @classmethod
    def _external_mcp_link_marker(
        cls,
        phase: float,
        colors: dict[str, str]
    ) -> tuple[str, str]:
        pulse  = 0.5 + (0.5 * math.sin((phase * 2.4) + 0.45))
        glyphs = ("-", "\\", "|", "/")
        glyph  = glyphs[int(max(0.0, phase) * 4.2) % len(glyphs)]
        color  = mix_hex_color(colors["spin_dim"], colors["spin"], 0.22 + (pulse * 0.78))

        return glyph, f"bold {color}"

    @classmethod
    def inbuild_startup_renderable(
        cls,
        phase: float,
        snapshot: dict[str, typing.Any
        ]
    ) -> Text:
        state = str(snapshot.get("state") or "starting").strip().lower()
        title = str(snapshot.get("label") or "Internal MCP").strip() or "Internal MCP"

        colors = {
            "spin"      : "#7DD3FC",
            "spin_dim"  : "#425466",
            "done"      : "#7EE787",
            "fail"      : "#FF5F5F",
            "text_peak" : "#F2F8FF",
            "text"      : "#D7E6F2",
            "text_near" : "#A9C7DC",
            "text_mid"  : "#7893A6",
            "text_dim"  : "#526575",
            "fail_text" : "#FF8A8A",
            "detail"    : "#D98A8A",
            "branch"    : "#8FA4B8",
        }

        out = Text()
        if state == "ready":
            out.append("■", style=f"bold {colors['done']}")
            out.append(f" {title} ready", style=f"bold {colors['text']}")
            return out

        if state == "failed":
            out.append("■", style=f"bold {colors['fail']}")
            out.append(f" {title} failed", style=f"bold {colors['fail_text']}")
            return out

        marker, marker_style = cls._external_mcp_link_marker(phase, colors)
        out.append(marker, style=marker_style)
        out.append(" ", style=f"bold {colors['spin_dim']}")

        label = f"{title} starting"

        cls._append_gradient_sweep_text(
            out,
            label,
            focus=cls._external_mcp_link_focus(phase, max(1, len(label))),
            peak_color=colors["text_peak"],
            soft_color=colors["text"],
            near_color=colors["text_near"],
            mid_color=colors["text_mid"],
            fade_color="#60798B",
            dim_color=colors["text_dim"],
            lead_span=cls.STARTUP_SWEEP_LEAD_SPAN,
            tail_span=cls.STARTUP_SWEEP_TAIL_SPAN,
            peak_radius=cls.STARTUP_SWEEP_PEAK_RADIUS,
        )
        return out

    @classmethod
    def _external_mcp_status_parts(
        cls,
        snapshot: dict[str, typing.Any],
    ) -> tuple[str, list[dict[str, str]]]:
        summary_override = str(snapshot.get("summary") or "").strip()
        items = [
            item for item in list(snapshot.get("items") or [])
            if isinstance(item, dict)
        ]
        if not items:
            return summary_override, []

        done = bool(snapshot.get("done", False))

        ready_count: int     = 0
        total_tools: int     = 0
        connected_count: int = 0

        failed_names: list[str] = []

        for item in items:
            state = str(item.get("state") or "").lower()
            if state in {"ready", "empty"}:
                connected_count += 1
            elif state == "failed":
                name = str(item.get("name") or "server").strip() or "server"
                failed_names.append(name)
            if state != "ready":
                continue
            try:
                tool_count = int(item.get("tools") or 0)
                if tool_count > 0:
                    ready_count += 1
                    total_tools += tool_count
            except (TypeError, ValueError, OverflowError):
                continue

        if done:
            if ready_count > 0:
                prefix = "External MCP ready"
            elif connected_count > 0:
                prefix = "External MCP available"
            else:
                prefix = "External MCP failed"
        else:
            prefix = "External MCP linking"

        parts = [prefix, f"{ready_count}/{len(items)} servers" if done else f"{len(items)} servers"]
        if total_tools > 0:
            parts.append(f"{total_tools} tools")

        show_details = done and bool(failed_names)
        if not show_details:
            return summary_override or " · ".join(parts), []

        detail_items = [
            item for item in items
            if str(item.get("state") or "").strip().lower() == "failed"
        ]

        detail_limit = min(5, len(detail_items))
        visible_count = detail_limit + (1 if len(detail_items) > detail_limit else 0)
        details      = []

        for index, item in enumerate(detail_items[:detail_limit]):
            connector = cls._external_mcp_detail_connector(index, visible_count)
            details.append(
                {
                    "text"  : f"{connector}{cls._external_mcp_item_text(item)}",
                    "state" : str(item.get("state") or "").strip().lower()
                }
            )
        if len(detail_items) > detail_limit:
            connector = cls._external_mcp_detail_connector(detail_limit, visible_count)
            details.append(
                {
                    "text"  : f"{connector}... {len(detail_items) - detail_limit} more servers",
                    "state" : "more"
                }
            )

        return summary_override or " · ".join(parts), details

    @classmethod
    def _external_mcp_details(
        cls,
        out: Text,
        details: list[dict[str, str]],
        colors: dict[str, str],
    ) -> None:
        if not details:
            return None

        console_width = 80
        if cls.console is not None:
            console_width = max(24, int(cls.console.width))
        limit = max(12, min(72, console_width - 3))

        for detail in details:
            fitted = cls.truncate_status_text(detail.get("text", ""), limit=limit)
            out.append("\n", style=f"bold {colors['detail_dim']}")
            style = cls._external_mcp_detail_color(detail.get("state", ""), fitted, colors)
            out.append(fitted, style=f"bold {style}")
        return None

    @classmethod
    def _external_mcp_done_level(
        cls,
        snapshot: dict[str, typing.Any]
    ) -> str:
        items = [
            item for item in list(snapshot.get("items") or [])
            if isinstance(item, dict)
        ]
        if not items:
            return "ready"

        connected_count: int = 0
        failed_count: int    = 0

        for item in items:
            state = str(item.get("state") or "").strip().lower()
            if state in {"ready", "empty"}:
                connected_count += 1
            elif state == "failed":
                failed_count += 1

        if failed_count and connected_count <= 0:
            return "failed"
        if failed_count:
            return "warn"

        return "ready"

    @classmethod
    def external_mcp_status_text(
        cls,
        snapshot: dict[str, typing.Any]
    ) -> str:
        summary, details = cls._external_mcp_status_parts(snapshot)

        lines = [
            str(detail.get("text") or "")
            for detail in details
            if str(detail.get("text") or "").strip()
        ]

        return "\n".join([summary, *lines]) if summary else ""

    @classmethod
    def external_mcp_renderable(
        cls,
        phase: float,
        snapshot: dict[str, typing.Any]
    ) -> Text:
        summary, details = cls._external_mcp_status_parts(snapshot)

        done = bool(snapshot.get("done", False))

        colors = {
            "spin"         : "#7DD3FC",
            "done"         : "#7EE787",
            "warn"         : "#D8B26E",
            "fail"         : "#FF5F5F",
            "spin_dim"     : "#425466",
            "text_peak"    : "#F2F8FF",
            "text_soft"    : "#D7E6F2",
            "text_near"    : "#A9C7DC",
            "text_mid"     : "#7893A6",
            "text_dim"     : "#526575",
            "detail"       : "#9FB3C3",
            "detail_dim"   : "#617281",
            "detail_ready" : "#8BD49C",
            "detail_wait"  : "#8FC7EA",
            "detail_warn"  : "#8A7A5A",
            "detail_fail"  : "#D88C8C",
        }

        out = Text()
        if done:
            done_level = cls._external_mcp_done_level(snapshot)
            if done_level == "failed":
                out.append("■", style=f"bold {colors['fail']}")
            elif done_level == "warn":
                out.append("■", style=f"bold {colors['warn']}")
            else:
                out.append("■", style=f"bold {colors['done']}")
        else:
            marker, marker_style = cls._external_mcp_link_marker(phase, colors)
            out.append(marker, style=marker_style)

        out.append(" ", style=f"bold {colors['spin_dim']}")

        if not summary:
            return out

        console_width = 80
        if cls.console is not None:
            console_width = max(24, int(cls.console.width))

        fitted = cls.truncate_status_text(
            summary,
            limit=max(12, min(72, console_width - 3))
        )
        span = max(1, len(fitted))

        if done:
            for char in fitted:
                if char == "·":
                    out.append(char, style=f"bold {colors['text_mid']}")
                else:
                    out.append(char, style=f"bold {colors['text_soft']}")
            cls._external_mcp_details(out, details, colors)
            return out

        focus = cls._external_mcp_link_focus(phase, span)

        cls._append_gradient_sweep_text(
            out,
            fitted,
            focus=focus,
            peak_color="#F6FBFF",
            soft_color=colors["text_soft"],
            near_color=colors["text_near"],
            mid_color=colors["text_mid"],
            fade_color="#60798B",
            dim_color=colors["text_dim"],
            lead_span=cls.STARTUP_SWEEP_LEAD_SPAN,
            tail_span=cls.STARTUP_SWEEP_TAIL_SPAN,
            peak_radius=cls.STARTUP_SWEEP_PEAK_RADIUS,
        )
        cls._external_mcp_details(out, details, colors)
        return out

    async def inbuild_startup_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """内置运行时启动状态，最终保留为单行。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        fps        = 30
        loop       = asyncio.get_running_loop()
        started_at = loop.time()

        with Live(
            self.inbuild_startup_renderable(0.0, snapshot()),
            console=self.console,
            refresh_per_second=fps,
            transient=False
        ) as live:
            while not stop_event.is_set():
                phase = max(0.0, loop.time() - started_at)
                live.update(self.inbuild_startup_renderable(phase, snapshot()))
                await asyncio.sleep(1 / fps)

            phase = max(0.0, loop.time() - started_at)
            live.update(self.inbuild_startup_renderable(phase, snapshot()))

        if self.console is not None:
            self.console.print()

    async def external_mcp_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """外部 MCP 启动状态。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        fps           = 30
        loop          = asyncio.get_running_loop()
        started_at    = loop.time()
        phase_bias    = 0.08
        last_snapshot = snapshot()

        with Live(
            self.external_mcp_renderable(0.0, last_snapshot),
            console=self.console,
            refresh_per_second=fps,
            transient=False
        ) as live:
            while not stop_event.is_set():
                last_snapshot = snapshot()
                phase = max(0.0, loop.time() - started_at) + phase_bias
                live.update(self.external_mcp_renderable(phase, last_snapshot))
                await asyncio.sleep(1 / fps)

            last_snapshot = snapshot()

            phase = max(0.0, loop.time() - started_at) + phase_bias
            live.update(self.external_mcp_renderable(phase, last_snapshot))
            elapsed = loop.time() - started_at
            await asyncio.sleep(max(0.18, 0.80 - elapsed))

        if self.console is not None:
            self.console.print()

    async def agent_wait_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], tuple[str, str]]
    ) -> None:
        """订阅模式呼吸等待动画。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        width = min(34, max(24, self.console.width - 22))
        theme = AgentLiveTheme(
            refresh_per_second=32,
            text_width=min(56, max(28, self.console.width - 10)),
            base_pad="  ",
            colors={
                "shell"      : "#223444",
                "shell_dim"  : "#13202C",
                "core"       : "#D9FCFF",
                "near"       : "#56D8E8",
                "beam"       : "#35C2DB",
                "beam_dim"   : "#35586A",
                "title"      : "#DDFBFF",
                "detail"     : "#94A6BA",
                "detail_dim" : "#60758C",
                "pulse"      : "#6BE2FF",
                "pulse_dim"  : "#3E6F8E"
            },
            default_title="Subscription Idle",
            default_detail="Waiting for link state"
        )

        tick = 0
        with Live(
            render_agent_wait_frame(0, snapshot(), width, theme),
            console=self.console,
            refresh_per_second=theme.refresh_per_second,
            transient=True
        ) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(render_agent_wait_frame(tick, snapshot(), width, theme))
                await asyncio.sleep(1 / theme.refresh_per_second)

    async def agent_connect_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], tuple[str, str]]
    ) -> None:
        """订阅模式建连等待动画。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        width = min(34, max(24, self.console.width - 22))
        theme = AgentLiveTheme(
            refresh_per_second=30,
            text_width=min(56, max(28, self.console.width - 10)),
            base_pad="  ",
            colors={
                "pulse"      : "#7EDCFF",
                "pulse_dim"  : "#4E7A9D",
                "title"      : "#E8F9FF",
                "detail"     : "#A8B6C8",
                "detail_dim" : "#6E8095",
                "node"       : "#48C8FF",
                "node_hot"   : "#BDF4FF",
                "rail_dim"   : "#284055",
                "rail_hot"   : "#63D7F4"
            },
            default_title="Opening Fold Link",
            default_detail="Waiting for subscription handshake"
        )
        tick = 0
        with Live(
            render_agent_connect_frame(0, snapshot(), width, theme),
            console=self.console,
            refresh_per_second=theme.refresh_per_second,
            transient=True
        ) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(render_agent_connect_frame(tick, snapshot(), width, theme))
                await asyncio.sleep(1 / theme.refresh_per_second)

    async def stream_mode_live(
        self,
        stop_event: asyncio.Event,
        mode: RunMode = DEFAULT_RUN_MODE
    ) -> None:
        """主请求模式等待动画。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        kind = "mode"

        label    = self.mode_status_text(mode)
        fps      = self.status_refresh_per_second(kind)
        interval = self.status_interval(kind)
        phase    = 0.0

        def frame(sec: float) -> Text:
            renderable = self.mode_status_renderable(phase, label)
            renderable.append_text(self.status_elapsed_renderable(sec))
            return renderable

        loop = asyncio.get_running_loop()

        started_at = loop.time()
        with Live(
            frame(0.0),
            console=self.console,
            refresh_per_second=fps,
            transient=True
        ) as live:
            while not stop_event.is_set():
                phase += self.status_step(kind) * interval
                elapsed_sec = loop.time() - started_at
                live.update(frame(elapsed_sec))
                await asyncio.sleep(interval)


if __name__ == '__main__':
    pass
