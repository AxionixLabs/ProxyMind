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

    async def stream_wait_live(
        self,
        stop_event: asyncio.Event,
        theme: RunMode = "chat"
    ) -> None:
        """流式等待动画效果。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        palettes: dict[str, dict[str, typing.Any]] = {
            "chat": {
                "glyphs": {
                    "spin"         : "◜◠◝◞◡◟",
                    "bubble_left"  : "〈《(",
                    "bubble_right" : ")》〉",
                    "bubble_dot"   : "●◉",
                    "focus"        : "◆",
                    "pulse"        : "•",
                    "echo"         : "·",
                    "beam_a"       : "═",
                    "beam_b"       : "─",
                    "noise"        : "˙",
                    "tail"         : "•",
                    "trail"        : "⋅",
                    "reply"        : "◦◎",
                    "listen"       : "◌◍",
                    "speak"        : "◉◍"
                },
                "colors": {
                    "prefix"     : "#A3E635",
                    "core"       : "#C4FFF0",
                    "near"       : "#9EF7E7",
                    "beam"       : "#67E8F9",
                    "beam_dim"   : "#3F9FB3",
                    "dust"       : "#3F3F46",
                    "sweep_core" : "#93C5FD",
                    "sweep_tail" : "#60A5FA",
                    "shell"      : "#244454",
                    "shell_dim"  : "#22313A",
                    "orbit_a"    : "#FDE68A",
                    "orbit_b"    : "#8BE9FD",
                    "orbit_c"    : "#5EEAD4"
                },
                "motion": {
                    "phase_div"      : 4.2,
                    "lead_freq"      : 1.08,
                    "reply_freq"     : 0.72,
                    "reply_phase"    : 1.45,
                    "breathe_freq"   : 0.88,
                    "sender_freq"    : 1.62,
                    "receiver_freq"  : 1.28,
                    "receiver_phase" : 2.2,
                    "chat_cycle"     : 16
                }
            },
            "fast": {
                "glyphs": {
                    "spin"   : "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
                    "packet" : "◈",
                    "core"   : "◆",
                    "near"   : "•",
                    "beam_a" : "=",
                    "beam_b" : "-",
                    "trail"  : ":",
                    "echo"   : "~",
                    "gate"   : ">",
                    "dust"   : "˙",
                    "orbit"  : ".",
                    "glitch" : "·:~"
                },
                "colors": {
                    "prefix"     : "#F59E0B",
                    "core"       : "#FFF3C4",
                    "near"       : "#FCD34D",
                    "beam"       : "#FB7185",
                    "beam_dim"   : "#BE5672",
                    "dust"       : "#4A2D33",
                    "sweep_core" : "#F97316",
                    "sweep_tail" : "#FB7185",
                    "shell"      : "#5B2C1A",
                    "shell_dim"  : "#3A2320",
                    "orbit_a"    : "#FDBA74",
                    "orbit_b"    : "#F472B6",
                    "orbit_c"    : "#FDE68A"
                },
                "motion": {
                    "phase_div"    : 3.0,
                    "lead_freq"    : 0.92,
                    "echo_phase"   : 0.85,
                    "pilot_freq"   : 1.8,
                    "pilot_offset" : 3.0,
                    "pilot_amp"    : 1.5
                }
            },
            "xtra": {
                "glyphs": {
                    "spin"       : "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
                    "hub"        : "◈",
                    "hub_hot"    : "◆",
                    "port"       : "◇",
                    "port_hot"   : "◉",
                    "node"       : "○",
                    "node_hot"   : "●",
                    "beam_a"     : "╍",
                    "beam_b"     : "─",
                    "bridge"     : "╼",
                    "pulse"      : "•",
                    "echo"       : "·",
                    "dust"       : "˙",
                    "probe"      : "⌁",
                    "scan"       : "⌕",
                    "gate_left"  : "‹",
                    "gate_right" : "›"
                },
                "colors": {
                    "prefix"     : "#2DAA9E",
                    "core"       : "#D6FFFA",
                    "near"       : "#88F0E4",
                    "beam"       : "#4DD6C9",
                    "beam_dim"   : "#2C8F86",
                    "dust"       : "#21413E",
                    "sweep_core" : "#7FFBF1",
                    "sweep_tail" : "#33C7B8",
                    "shell"      : "#17403C",
                    "shell_dim"  : "#13302D",
                    "orbit_a"    : "#A7F3D0",
                    "orbit_b"    : "#93C5FD",
                    "orbit_c"    : "#C4B5FD"
                },
                "motion": {
                    "phase_div"   : 4.8,
                    "scan_freq"   : 1.12,
                    "hub_freq"    : 1.7,
                    "port_freq"   : 0.72,
                    "bridge_freq" : 1.36,
                    "probe_freq"  : 1.9
                }
            }
        }
        palette = palettes.get(theme, palettes["chat"])

        glyphs: dict[str, str] = palette["glyphs"]
        colors: dict[str, str] = palette["colors"]
        motion: dict[str, float] = palette["motion"]
        width = min(30, max(22, self.console.width - 22))

        fps = 32
        spin = glyphs["spin"]
        tick = 0

        def clamp(pos: int) -> int:
            return max(0, min(width - 1, pos))

        def build_chat(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            cycle = max(12, int(motion["chat_cycle"]))
            beat  = i % cycle

            sender_talk     = beat in {1, 2, 3, 4, 5, 6, 7}
            sender_release  = beat in {8, 9}
            receiver_listen = beat in {10, 11, 12, 13}
            receiver_ack    = beat in {14, 15, 16, 17}

            left_rest      = 2
            right_rest     = width - 3
            sender_shift   = -1 if sender_talk else 0
            receiver_shift = 1 if receiver_ack else 0

            left       = clamp(left_rest + sender_shift)
            right      = clamp(right_rest + receiver_shift)
            lane_start = left + 2
            lane_end   = right - 2
            lane_span  = max(1, lane_end - lane_start)
            lead       = 0.5 + 0.5 * math.sin(phase * motion["lead_freq"])
            head_ratio = 0.10 + 0.78 * lead

            if sender_talk:
                head_ratio = min(0.96, head_ratio + 0.06)

            head       = clamp(lane_start + int(lane_span * head_ratio))
            tail_len   = min(8, max(4, width // 4))
            reply_gate = 0.5 + 0.5 * math.sin((phase * motion["reply_freq"]) + motion["reply_phase"])
            reply_head = clamp(lane_end - int((lane_span * 0.22) * reply_gate))
            chars      = [" "] * width

            styles: dict[int, str] = {}

            left_shell       = glyphs["bubble_left"][1 if sender_talk else (2 if sender_release else 0)]
            right_shell      = glyphs["bubble_right"][1 if receiver_ack else (2 if receiver_listen else 0)]
            left_dot_idle    = glyphs["bubble_dot"][0]
            left_dot_talk    = glyphs["speak"][0] if (i % 4) < 2 else glyphs["speak"][1]
            right_dot_idle   = glyphs["listen"][0]
            right_dot_listen = glyphs["listen"][1] if (i % 4) < 2 else glyphs["pulse"]
            reply_glyph      = glyphs["reply"][1] if (i % 4) < 2 else glyphs["reply"][0]

            chars[left] = left_shell
            styles[left] = (
                f"bold {colors['core']}" if sender_talk
                else (f"bold {colors['near']}" if sender_release else f"bold {colors['orbit_a']}")
            )

            left_dot = clamp(left + 1)
            chars[left_dot] = left_dot_talk if sender_talk else (glyphs["tail"] if sender_release else left_dot_idle)
            styles[left_dot] = (
                f"bold {colors['sweep_core']}" if sender_talk
                else (f"bold {colors['sweep_tail']}" if sender_release else f"bold {colors['core']}")
            )

            left_aura = clamp(left + 2)
            if sender_talk:
                chars[left_aura] = glyphs["tail"] if (i % 4) in {1, 2} else glyphs["trail"]
                styles[left_aura] = f"bold {colors['sweep_tail']}"
            elif sender_release and (i % 2 == 0):
                chars[left_aura] = glyphs["trail"]
                styles[left_aura] = f"bold {colors['beam_dim']}"

            chars[right] = right_shell
            styles[right] = (
                f"bold {colors['orbit_b']}" if receiver_ack
                else (f"bold {colors['near']}" if receiver_listen else f"bold {colors['orbit_c']}")
            )

            right_dot = clamp(right - 1)
            chars[right_dot] = reply_glyph if receiver_ack else (right_dot_listen if receiver_listen else right_dot_idle)
            styles[right_dot] = (
                f"bold {colors['orbit_b']}" if receiver_ack
                else (f"bold {colors['core']}" if receiver_listen else f"bold {colors['near']}")
            )

            right_aura = clamp(right - 2)
            if receiver_ack:
                chars[right_aura] = glyphs["pulse"] if (i % 4) in {1, 2} else reply_glyph
                styles[right_aura] = f"bold {colors['orbit_b']}"
            elif receiver_listen and (i % 2 == 0):
                chars[right_aura] = glyphs["listen"][0]
                styles[right_aura] = f"bold {colors['beam_dim']}"

            for pos in range(lane_start, lane_end + 1):
                dist = head - pos
                reply_dist = abs(pos - reply_head)

                if pos == head:
                    chars[pos] = glyphs["focus"]
                    styles[pos] = f"bold {colors['sweep_core']}"
                elif 0 < dist <= 2:
                    chars[pos] = glyphs["tail"]
                    styles[pos] = f"bold {colors['core']}"
                elif 0 < dist <= tail_len:
                    chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                    styles[pos] = f"bold {colors['beam']}"
                elif 0 < dist <= tail_len + 4:
                    if (pos + i) % 2 != 0:
                        continue
                    chars[pos] = glyphs["trail"]
                    styles[pos] = f"bold {colors['beam_dim']}"
                elif head < pos <= min(lane_end, head + 2):
                    chars[pos] = glyphs["echo"]
                    styles[pos] = f"bold {colors['sweep_tail']}"
                elif pos >= right - 5 and reply_dist == 0 and receiver_ack:
                    chars[pos] = reply_glyph
                    styles[pos] = f"bold {colors['orbit_b']}"
                elif pos >= right - 5 and reply_dist <= 1 and receiver_ack and (i + pos) % 2 == 0:
                    chars[pos] = glyphs["pulse"]
                    styles[pos] = f"bold {colors['near']}"
                elif pos >= right - 6 and receiver_listen and (pos + i) % 2 == 0:
                    chars[pos] = glyphs["listen"][0]
                    styles[pos] = f"bold {colors['beam_dim']}"
                elif pos >= right - 7 and (pos + i) % 3 == 0:
                    chars[pos] = glyphs["echo"]
                    styles[pos] = f"bold {colors['shell']}"
                elif pos <= left + 4 and sender_talk and (pos + i) % 2 == 0:
                    chars[pos] = glyphs["trail"] if (i + pos) % 4 == 0 else glyphs["pulse"]
                    styles[pos] = f"bold {colors['shell_dim']}"
                elif (pos + i) % 7 == 0:
                    chars[pos] = glyphs["noise"]
                    styles[pos] = f"bold {colors['dust']}"

            for pos in (clamp(left - 1), clamp(left - 2), clamp(right + 1), clamp(right + 2)):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["noise"]
                styles[pos] = f"bold {colors['shell_dim']}"

            if sender_talk:
                ember = clamp(left - 1)
                chars[ember] = glyphs["trail"]
                styles[ember] = f"bold {colors['beam_dim']}"
            elif sender_release:
                ember = clamp(left - 1)
                chars[ember] = glyphs["echo"]
                styles[ember] = f"bold {colors['shell_dim']}"

            if receiver_ack:
                wink = clamp(right + 1)
                chars[wink] = reply_glyph
                styles[wink] = f"bold {colors['orbit_b']}"
            elif receiver_listen:
                wink = clamp(right + 1)
                chars[wink] = glyphs["trail"]
                styles[wink] = f"bold {colors['shell_dim']}"

            mid = clamp((lane_start + lane_end) // 2)
            if not chars[mid].strip() and abs(mid - head) > 3 and (i // 2) % 2 == 0:
                chars[mid] = glyphs["trail"]
                styles[mid] = f"bold {colors['beam_dim']}"
            return chars, styles

        def build_fast(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            lead  = 0.5 + 0.5 * math.sin(phase * motion["lead_freq"])
            head  = clamp(int(lead * (width - 1)))

            echo = clamp(
                int((0.5 + 0.5 * math.sin((phase * motion["lead_freq"]) + motion["echo_phase"])) * (width - 1))
            )

            pilot = clamp(min(
                width - 1,
                head + int(motion["pilot_offset"] + motion["pilot_amp"] * math.sin(phase * motion["pilot_freq"]))
            ))

            chars  = [" "] * width
            glitch = glyphs["glitch"]

            styles: dict[int, str] = {}

            for pos in range(width):
                dist = abs(pos - head)
                if dist == 0:
                    chars[pos] = glyphs["packet"]
                    styles[pos] = f"bold {colors['sweep_core']}"
                elif dist <= 1:
                    chars[pos] = glyphs["core"]
                    styles[pos] = f"bold {colors['core']}"
                elif dist <= 2:
                    chars[pos] = glyphs["near"]
                    styles[pos] = f"bold {colors['near']}"
                elif pos < head and (head - pos) <= 10:
                    tail = head - pos
                    if tail <= 2:
                        chars[pos] = glyphs["near"]
                        styles[pos] = f"bold {colors['near']}"
                    elif tail <= 5:
                        chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                        styles[pos] = f"bold {colors['beam']}"
                    else:
                        chars[pos] = glitch[(pos + i) % len(glitch)]
                        styles[pos] = f"bold {colors['beam_dim']}"
                elif pos > head and (pos - head) <= 2:
                    chars[pos] = glyphs["trail"]
                    styles[pos] = f"bold {colors['sweep_tail']}"
                elif pos < head and (head - pos) <= 14 and (pos + i) % 4 == 0:
                    chars[pos] = glitch[(head - pos + i) % len(glitch)]
                    styles[pos] = f"bold {colors['dust']}"
                elif pos > head and (pos - head) <= 6 and (pos + i) % 3 == 0:
                    chars[pos] = glyphs["dust"]
                    styles[pos] = f"bold {colors['shell_dim']}"
                elif (pos + i) % 9 == 0:
                    chars[pos] = glyphs["dust"]
                    styles[pos] = f"bold {colors['dust']}"

            if not chars[echo].strip():
                chars[echo] = glyphs["echo"]
                styles[echo] = f"bold {colors['beam_dim']}"

            if not chars[pilot].strip():
                chars[pilot] = glyphs["trail"]
                styles[pilot] = f"bold {colors['sweep_tail']}"

            for pos, color in (
                (clamp(head - 12), "orbit_a"),
                (clamp(head - 7), "orbit_b"),
                (clamp(head + 7), "orbit_c"),
            ):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["orbit"]
                styles[pos] = f"bold {colors[color]}"

            if head > 2:
                gate = clamp(head - 2)
                if not chars[gate].strip():
                    chars[gate] = glyphs["gate"]
                    styles[gate] = f"bold {colors['beam']}"

            return chars, styles

        def build_plan(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            cols  = [2, width // 3, (2 * width) // 3, width - 3]
            chars = [" "] * width

            styles: dict[int, str] = {}

            active   = int((0.5 + 0.5 * math.sin(phase * motion["active_freq"])) * (len(cols) - 1) + 0.5)
            bridge   = 0.5 + 0.5 * math.sin(phase * motion["bridge_freq"])
            prev_idx = max(0, active - 1)
            next_idx = min(len(cols) - 1, active + 1)

            for idx, pos in enumerate(cols):
                if idx < active:
                    chars[pos] = glyphs["done"]
                    styles[pos] = f"bold {colors['beam']}"
                elif idx == active:
                    chars[pos] = glyphs["active"]
                    styles[pos] = f"bold {colors['core']}"
                elif idx == next_idx:
                    chars[pos] = glyphs["next"]
                    styles[pos] = f"bold {colors['near']}"
                else:
                    chars[pos] = glyphs["idle"]
                    styles[pos] = f"bold {colors['beam_dim']}"

            for idx in range(len(cols) - 1):
                a, b = cols[idx], cols[idx + 1]
                progress_pos = clamp(a + 1 + int((b - a - 2) * bridge))
                for pos in range(a + 1, b):
                    ratio = (pos - a) / max(b - a, 1)
                    if idx < active:
                        chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                        styles[pos] = f"bold {colors['beam']}"
                    elif idx == active and ratio <= bridge:
                        if pos == progress_pos:
                            chars[pos] = glyphs["progress"]
                            styles[pos] = f"bold {colors['sweep_core']}"
                        elif pos >= progress_pos - 2:
                            chars[pos] = glyphs["pulse"]
                            styles[pos] = f"bold {colors['sweep_tail']}"
                        else:
                            chars[pos] = glyphs["beam_b"]
                            styles[pos] = f"bold {colors['near']}"
                    elif idx == active:
                        chars[pos] = glyphs["echo"]
                        styles[pos] = f"bold {colors['shell']}"
                    elif (pos + i) % 6 == 0:
                        chars[pos] = glyphs["echo"]
                        styles[pos] = f"bold {colors['shell_dim']}"

            halo = [
                clamp(cols[active] - 2), clamp(cols[active] - 1),
                clamp(cols[active] + 1), clamp(cols[active] + 2)
            ]
            for idx, pos in enumerate(halo):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["echo"] if idx % 2 == 0 else glyphs["pulse"]
                styles[pos] = f"bold {(colors['orbit_a'], colors['orbit_b'], colors['orbit_c'], colors['orbit_a'])[idx]}"

            for pos in (clamp(cols[prev_idx] - 1), clamp(cols[next_idx] + 1)):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["echo"]
                styles[pos] = f"bold {colors['orbit_b']}"
            return chars, styles

        def build_xtra(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            chars = [" "] * width

            styles: dict[int, str] = {}

            hub        = width // 2
            left_gate  = 1
            right_gate = width - 2

            ports = [
                clamp(width // 5),
                clamp((2 * width) // 5),
                clamp((3 * width) // 5),
                clamp((4 * width) // 5),
            ]

            scan             = 0.5 + 0.5 * math.sin(phase * motion["scan_freq"])
            scan_pos         = clamp(2 + int(scan * max(1, width - 5)))
            reverse_scan_pos = clamp(width - 3 - int(scan * max(1, width - 5)))
            active_port      = int((0.5 + 0.5 * math.sin(phase * motion["port_freq"])) * (len(ports) - 1) + 0.5)
            bridge           = 0.5 + 0.5 * math.sin(phase * motion["bridge_freq"])
            hub_hot          = math.sin(phase * motion["hub_freq"]) > -0.15

            chars[left_gate]   = glyphs["gate_left"]
            styles[left_gate]  = f"bold {colors['beam_dim']}"
            chars[right_gate]  = glyphs["gate_right"]
            styles[right_gate] = f"bold {colors['beam_dim']}"

            chars[hub]  = glyphs["hub_hot"] if hub_hot else glyphs["hub"]
            styles[hub] = f"bold {colors['core'] if hub_hot else colors['near']}"

            for idx, pos in enumerate(ports):
                if pos == hub:
                    continue
                hot = idx == active_port

                chars[pos]  = glyphs["port_hot"] if hot else glyphs["port"]
                styles[pos] = f"bold {colors['sweep_core'] if hot else colors['orbit_b']}"

                a, b = sorted((pos, hub))
                span = max(1, b - a - 1)

                bridge_pos = clamp(a + 1 + int(span * bridge))
                for lane in range(a + 1, b):
                    if lane == bridge_pos and hot:
                        chars[lane] = glyphs["bridge"]
                        styles[lane] = f"bold {colors['sweep_tail']}"
                    elif abs(lane - bridge_pos) <= 1 and hot:
                        chars[lane] = glyphs["pulse"]
                        styles[lane] = f"bold {colors['beam']}"
                    elif (lane + i + idx) % 5 == 0:
                        chars[lane] = glyphs["beam_a"] if (lane + idx) % 2 == 0 else glyphs["beam_b"]
                        styles[lane] = f"bold {colors['beam_dim']}"

            for pos, color in (
                (scan_pos, "sweep_core"),
                (reverse_scan_pos, "orbit_a"),
                (clamp(hub - 2), "orbit_c"),
                (clamp(hub + 2), "orbit_c"),
            ):
                if chars[pos].strip():
                    continue
                chars[pos]  = glyphs["scan"] if color.startswith("sweep") else glyphs["echo"]
                styles[pos] = f"bold {colors[color]}"

            probe_span  = max(1, width - 6)
            probe_phase = int((0.5 + 0.5 * math.sin(phase * motion["probe_freq"])) * probe_span)

            for offset, color in ((0, "orbit_a"), (7, "orbit_b"), (13, "orbit_c")):
                pos = clamp(3 + ((probe_phase + offset + i // 3) % probe_span))
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["probe"] if (i + offset) % 3 == 0 else glyphs["dust"]
                styles[pos] = f"bold {colors[color]}"

            for side in (-1, 1):
                aura = clamp(hub + side * (3 + (i // 3) % 3))
                if not chars[aura].strip():
                    chars[aura] = glyphs["node_hot"] if hub_hot else glyphs["node"]
                    styles[aura] = f"bold {colors['shell'] if hub_hot else colors['shell_dim']}"

            return chars, styles

        def frame(i: int) -> Text:
            if theme == "xtra":
                chars, styles = build_xtra(i)
            elif theme == "fast":
                chars, styles = build_fast(i)
            else:
                chars, styles = build_chat(i)

            prefix = f"{spin[i % len(spin)]} "

            out = Text()
            out.append(prefix, style=f"bold {colors['prefix']}")

            for pos, ch in enumerate(chars):
                out.append(ch, style=styles.get(pos, "bold #2A2A2A"))
            return out

        async def settle(i: int) -> None:
            for step in range(7):
                fade = Text(" " * (width + 2), style="bold #202020")
                if step < 5:
                    live.update(frame(i + step))
                    await asyncio.sleep(0.016)
                live.update(fade)
                await asyncio.sleep(0.009)

        with Live(frame(0), console=self.console, refresh_per_second=fps, transient=True) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(frame(tick))
                await asyncio.sleep(1 / fps)

            await settle(tick)


if __name__ == '__main__':
    pass
