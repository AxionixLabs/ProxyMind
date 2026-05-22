# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from rich.text import Text
from rich.cells import cell_len
from .specs import StatusSpec
from ..utils import mix_hex_color


class StatusRenderer(StatusSpec):

    @classmethod
    def fit_status_text(cls, text: typing.Any, *, kind: str, fallback: str) -> str:
        fitted = cls.truncate_status_text(text, limit=cls.status_text_limit(kind)) or str(fallback)
        return cls.pad_status_text(fitted, kind=kind)

    @classmethod
    def pad_status_text(cls, text: str, *, kind: str) -> str:
        normalized = str(text or "")
        if not normalized:
            return ""

        floor = min(cls.status_text_limit(kind), cls.status_text_floor(kind))
        used_width = cell_len(normalized)
        if used_width >= floor:
            return normalized

        return f"{normalized}{' ' * (floor - used_width)}"

    @classmethod
    def truncate_status_text(cls, text: typing.Any, *, limit: int) -> str:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return ""
        width_limit = max(1, int(limit))
        if cell_len(normalized) <= width_limit:
            return normalized

        ellipsis_char = "…"
        ellipsis_width = cell_len(ellipsis_char)
        if width_limit <= ellipsis_width:
            return ellipsis_char

        body_limit = width_limit - ellipsis_width
        body_chars: list[str] = []
        used_width = 0

        for char in normalized:
            char_width = cell_len(char)
            if used_width + char_width > body_limit:
                break
            body_chars.append(char)
            used_width += char_width

        body = "".join(body_chars).rstrip()
        if not body:
            return ellipsis_char
        return f"{body}{ellipsis_char}"

    @classmethod
    def _append_sweep_text(
        cls,
        out: Text,
        text: str,
        *,
        focus: float,
        peak_style: str,
        dim_style: str,
        soft_style: str | None = None,
        near_style: str | None = None,
        mid_style: str | None = None,
        fade_style: str | None = None,
        lead_span: float = 2.0,
        tail_span: float = 3.5,
        peak_radius: float = 0.7,
        soft_ratio: float = 0.0,
        near_ratio: float = 0.42,
        mid_ratio: float = 1.0
    ) -> None:
        soft_style = soft_style or peak_style
        near_style = near_style or peak_style
        mid_style  = mid_style or dim_style
        fade_style = fade_style or dim_style

        for pos, char in enumerate(text):
            delta    = pos - focus
            span     = lead_span if delta >= 0 else tail_span
            distance = abs(delta)

            soft_limit = peak_radius + (span * max(0.0, soft_ratio))
            near_limit = peak_radius + (span * max(0.0, near_ratio))
            mid_limit  = peak_radius + (span * max(0.0, mid_ratio))

            if distance <= peak_radius:
                style = peak_style
            elif distance <= soft_limit:
                style = soft_style
            elif distance <= near_limit:
                style = near_style
            elif distance <= mid_limit:
                style = mid_style
            elif distance <= peak_radius + span:
                style = fade_style
            else:
                style = dim_style

            out.append(char, style=style)

    @classmethod
    def _append_gradient_sweep_text(
        cls,
        out: Text,
        text: str,
        *,
        focus: float,
        peak_color: str,
        soft_color: str,
        near_color: str,
        mid_color: str,
        fade_color: str,
        dim_color: str,
        lead_span: float,
        tail_span: float,
        peak_radius: float
    ) -> None:
        stops = (
            (0.00, dim_color),
            (0.18, fade_color),
            (0.44, mid_color),
            (0.72, near_color),
            (0.90, soft_color),
            (1.00, peak_color)
        )

        for pos, char in enumerate(text):
            distance = abs(pos - focus)
            span = lead_span if pos >= focus else tail_span
            if distance <= peak_radius:
                intensity = 1.0
            else:
                falloff = max(0.001, span)
                normalized = min(1.0, (distance - peak_radius) / falloff)
                intensity = 1.0 - cls._smoothstep(normalized)
                if pos < focus:
                    intensity *= 0.95

            out.append(char, style=cls._gradient_text_style(stops, intensity))

    @classmethod
    def _drift_focus(
        cls,
        phase: float,
        span: int,
        *,
        entry_pad: float,
        exit_pad: float
    ) -> float:
        left_pad = max(0.0, float(entry_pad))
        right_pad = max(0.0, float(exit_pad))
        travel = max(1.0, float(max(0, span - 1)) + left_pad + right_pad)
        return (phase % travel) - left_pad

    @classmethod
    def _gradient_text_style(
        cls,
        stops: tuple[tuple[float, str], ...],
        intensity: float
    ) -> str:
        level = max(0.0, min(1.0, float(intensity)))
        if level <= stops[0][0]:
            return f"bold {stops[0][1]}"

        for index in range(1, len(stops)):
            end_level, end_color = stops[index]
            start_level, start_color = stops[index - 1]
            if level <= end_level:
                span = max(0.0001, end_level - start_level)
                local = (level - start_level) / span
                eased = cls._smoothstep(local)
                color = mix_hex_color(start_color, end_color, eased)
                return f"bold {color}"

        return f"bold {stops[-1][1]}"

    @staticmethod
    def _smoothstep(value: float) -> float:
        clamped = max(0.0, min(1.0, float(value)))
        return clamped * clamped * (3.0 - (2.0 * clamped))

    @staticmethod
    def status_content_gap() -> str:
        return "  "

    @staticmethod
    def status_line_prefix() -> str:
        return " "

    @staticmethod
    def status_elapsed_separator() -> str:
        return "  · "

    @classmethod
    def status_line_renderable(
        cls,
        renderable: Text,
        *,
        prefix_style: str = "bold #46545C"
    ) -> Text:
        out = Text()
        prefix = cls.status_line_prefix()
        if prefix:
            out.append(prefix, style=prefix_style)
        out.append_text(renderable)
        return out

    @classmethod
    def _sway_focus(
        cls,
        phase: float,
        span: int,
        *,
        speed: float,
        pad: float = 0.0
    ) -> float:
        travel = max(1.0, float(span - 1) + (pad * 2.0))
        progress = 0.5 - (0.5 * math.cos(phase * speed))
        return (progress * travel) - pad

    @classmethod
    def _append_progressive_text(
        cls,
        out: Text,
        text: str,
        *,
        head: float,
        tail: float,
        peak_style: str,
        near_style: str,
        tail_style: str,
        dim_style: str,
        lead_glow: float = 0.4,
        tail_glow: float = 1.2
    ) -> None:
        for pos, char in enumerate(text):
            if char == " ":
                out.append(char, style=dim_style)
                continue

            if pos > head:
                style = near_style if (pos - head) <= lead_glow else dim_style
                out.append(char, style=style)
                continue

            if pos < tail:
                style = tail_style if (tail - pos) <= tail_glow else dim_style
                out.append(char, style=style)
                continue

            out.append(char, style=peak_style)

    @classmethod
    def _append_repair_text(
        cls,
        out: Text,
        text: str,
        *,
        left_focus: float,
        right_focus: float,
        peak_style: str,
        soft_style: str,
        near_style: str,
        mid_style: str,
        fade_style: str,
        dim_style: str,
        near_span: float,
        fade_span: float,
        peak_radius: float,
        lock_radius: float
    ) -> None:
        center = (max(0, len(text) - 1)) / 2.0
        focus_gap = abs(right_focus - left_focus)

        for pos, char in enumerate(text):
            left_distance = abs(pos - left_focus)
            right_distance = abs(pos - right_focus)
            distance = min(left_distance, right_distance)
            center_distance = abs(pos - center)

            if distance <= peak_radius:
                style = peak_style
            elif focus_gap <= lock_radius and center_distance <= max(1.0, peak_radius + 0.4):
                style = soft_style
            elif distance <= peak_radius + max(0.8, near_span * 0.22):
                style = soft_style
            elif distance <= peak_radius + max(1.4, near_span * 0.56):
                style = near_style
            elif distance <= peak_radius + max(2.1, fade_span * 0.58):
                style = mid_style
            elif distance <= peak_radius + max(3.0, fade_span):
                style = fade_style
            else:
                style = dim_style

            out.append(char, style=style)

    @staticmethod
    def _status_shell_char(
        level: float,
        *,
        solid: str = "o",
        soft: str = ".",
        idle: str = " ",
        solid_threshold: float = 0.90,
        soft_threshold: float = 0.74
    ) -> str:
        if level > solid_threshold:
            return solid
        if level > soft_threshold:
            return soft
        return idle

    @staticmethod
    def _status_core_char(
        level: float,
        *,
        peak: str = "*",
        high: str = "O",
        mid: str = "o",
        low: str = ".",
        super_peak: str | None = None,
        super_threshold: float = 0.94,
        peak_threshold: float = 0.80,
        high_threshold: float = 0.62,
        mid_threshold: float = 0.46
    ) -> str:
        if super_peak is not None and level > super_threshold:
            return super_peak
        if level > peak_threshold:
            return peak
        if level > high_threshold:
            return high
        if level > mid_threshold:
            return mid
        return low

    @staticmethod
    def _status_edge_pair(
        level: float,
        *,
        threshold: float = 0.84,
        active_left: str = "<",
        active_right: str = ">",
        idle_left: str = "[",
        idle_right: str = "]"
    ) -> tuple[str, str]:
        if level > threshold:
            return active_left, active_right
        return idle_left, idle_right

    @classmethod
    def _build_status_shell(
        cls,
        *,
        edge_left: str,
        edge_right: str,
        left_outer: str,
        left_inner: str,
        core: str,
        right_inner: str,
        right_outer: str,
        edge_style: str,
        outer_style: str,
        inner_style: str,
        core_style: str
    ) -> Text:
        out = Text()
        out.append(edge_left, style=edge_style)
        out.append(left_outer, style=outer_style if left_outer.strip() else edge_style)
        out.append(left_inner, style=inner_style if left_inner.strip() else edge_style)
        out.append(cls.status_content_gap(), style=edge_style)
        out.append(core, style=core_style)
        out.append(cls.status_content_gap(), style=edge_style)
        out.append(right_inner, style=inner_style if right_inner.strip() else edge_style)
        out.append(right_outer, style=outer_style if right_outer.strip() else edge_style)
        out.append(edge_right, style=edge_style)
        return out

    @classmethod
    def _tool_status_indicator(
        cls,
        phase: float,
        *,
        subtle: bool = False,
        breathe_freq: float = 0.52,
        frame_rate: float = 0.72
    ) -> Text:
        colors = {
            "edge"  : "bold #6A6256",
            "shell" : "bold #C7B8A1",
            "core"  : "bold #FFF7E7",
            "pulse" : "bold #F2DEC0",
            "dust"  : "bold #8F816E"
        }
        if subtle:
            breathe     = 0.46
            left_phase  = 0.58
            right_phase = 0.58
            shell_phase = 0.0
        else:
            breathe     = 0.5 + (0.5 * math.sin(phase * breathe_freq))
            left_phase  = 0.5 + (0.5 * math.sin((phase * breathe_freq) - 0.95))
            right_phase = 0.5 + (0.5 * math.sin((phase * breathe_freq) + 0.95))
            shell_phase = phase * frame_rate

        shell_frames = (
            ("(", ")"),
            ("(", ")"),
            ("(", ")"),
            ("<", ">"),
            ("(", ")"),
            ("{", "}"),
            ("(", ")"),
            ("<", ">"),
            ("(", ")"),
        )
        shell_left, shell_right = ("(", ")") if subtle else shell_frames[
            int(shell_phase) % len(shell_frames)
        ]

        if left_phase > 0.92:
            chamber_left = "o"
        elif left_phase > 0.82:
            chamber_left = "."
        else:
            chamber_left = " "

        if right_phase > 0.92:
            chamber_right = "o"
        elif right_phase > 0.82:
            chamber_right = "."
        else:
            chamber_right = " "

        if subtle:
            core = "o"
            echo_left = " "
            echo_right = " "
        else:
            core = cls._status_core_char(
                breathe,
                super_peak="@",
                peak="*",
                high="O",
                mid="o",
                low="."
            )

            if breathe > 0.91:
                echo_left = "."
                echo_right = "."
            else:
                echo_left = " "
                echo_right = " "
        out = Text()

        out.append("[", style=colors["edge"])
        out.append(echo_left, style=colors["dust"] if echo_left.strip() else colors["edge"])
        out.append(chamber_left, style=colors["dust"])
        out.append(shell_left, style=colors["shell"] if breathe < 0.84 else colors["pulse"])
        out.append(core, style=colors["core"] if breathe > 0.64 else colors["pulse"])
        out.append(shell_right, style=colors["shell"] if breathe < 0.84 else colors["pulse"])
        out.append(chamber_right, style=colors["dust"])
        out.append(echo_right, style=colors["dust"] if echo_right.strip() else colors["edge"])
        out.append("]", style=colors["edge"])
        return out

    @classmethod
    def _heal_status_indicator(
        cls,
        phase: float,
        *,
        pulse_freq: float = 0.84,
        frame_rate: float = 0.38
    ) -> Text:
        colors = {
            "edge"      : "bold #3C535B",
            "shell"     : "bold #58C2CF",
            "pulse"     : "bold #8CEAF4",
            "core"      : "bold #E2FCFF",
            "core_lock" : "bold #F4FFFF",
            "dust"      : "bold #486A71"
        }
        repair = 0.5 + (0.5 * math.sin(phase * pulse_freq))
        lock = cls._smoothstep(repair)
        shell_phase = phase * frame_rate

        shell_frames = (
            ("╱", "╲"),
            ("╱", "╲"),
            ("⟋", "⟍"),
            ("(", ")"),
            ("{", "}"),
            ("(", ")"),
            ("⟋", "⟍")
        )
        shell_left, shell_right = shell_frames[int(shell_phase) % len(shell_frames)]

        if lock > 0.86:
            inner_left = "─"
            inner_right = "─"
            center = "◉"
        elif lock > 0.72:
            inner_left = "╲"
            inner_right = "╱"
            center = "·"
        elif lock > 0.56:
            inner_left = "╲"
            inner_right = "╱"
            center = "╳"
        elif lock > 0.38:
            inner_left = "."
            inner_right = "."
            center = "·"
        else:
            inner_left = " "
            inner_right = " "
            center = " "

        if lock > 0.90:
            echo_left = "·"
            echo_right = "·"
        elif lock > 0.68:
            echo_left = "."
            echo_right = "."
        else:
            echo_left = " "
            echo_right = " "

        out = Text()
        out.append("[", style=colors["edge"])
        out.append(echo_left, style=colors["dust"] if echo_left.strip() else colors["edge"])
        out.append(shell_left, style=colors["pulse"] if lock > 0.80 else colors["shell"])
        out.append(inner_left, style=colors["shell"] if inner_left.strip() else colors["edge"])
        out.append(
            center,
            style=colors["core_lock"] if lock > 0.88 else (colors["core"] if lock > 0.74 else colors["pulse"])
        )
        out.append(inner_right, style=colors["shell"] if inner_right.strip() else colors["edge"])
        out.append(shell_right, style=colors["pulse"] if lock > 0.80 else colors["shell"])
        out.append(echo_right, style=colors["dust"] if echo_right.strip() else colors["edge"])
        out.append("]", style=colors["edge"])
        return out

    @classmethod
    def _loop_status_indicator(cls, phase: float) -> Text:
        profile = cls._loop_motion_profile(phase)

        pulse = 0.0
        if profile["stage"] == "linger":
            pulse = 0.5 + (0.5 * math.sin(float(profile["stage_progress"]) * math.tau * 1.15))
        elif profile["stage"] == "idle":
            pulse = 0.5 + (0.5 * math.sin(float(profile["stage_progress"]) * math.tau))

        colors = {
            "edge"      : "bold #465661",
            "rail"      : "bold #607884",
            "cell_dim"  : "bold #34444C",
            "cell_tail" : "bold #587E8C",
            "cell_near" : "bold #7CB8C5",
            "cell_peak" : "bold #C6FBFF"
        }
        if pulse > 0.0:
            cell_pulse = pulse * 0.82
            colors["cell_tail"] = f"bold {mix_hex_color('#587E8C', '#66C4D4', cell_pulse)}"
            colors["cell_near"] = f"bold {mix_hex_color('#7CB8C5', '#7FD9E6', cell_pulse)}"
            colors["cell_peak"] = f"bold {mix_hex_color('#C6FBFF', '#9FE8F2', cell_pulse)}"
        cycle = 7
        stage = profile["stage"]
        progress = float(profile["motion"])
        elapsed_sec = float(profile["elapsed_sec"])

        if stage == "converge":
            left_wave = progress
            right_wave = 1.0 - progress
        elif stage == "linger":
            breath = math.sin(float(profile["stage_progress"]) * math.tau * 1.15)
            center = 0.5 + (0.04 * breath)
            left_wave = center
            right_wave = center
        elif stage == "release":
            release_progress = max(0.0, min(1.0, (progress - 0.5) / 0.5))
            spread = cls._smoothstep(release_progress)
            left_wave = 0.5 - (0.5 * spread)
            right_wave = 0.5 + (0.5 * spread)
        else:
            left_wave = (
                0.08
                + (0.18 * (0.5 + 0.5 * math.sin((elapsed_sec * 1.27) + 0.35)))
                + (0.05 * math.sin((elapsed_sec * 2.73) + 1.10))
            )
            right_wave = (
                0.92
                - (0.18 * (0.5 + 0.5 * math.sin((elapsed_sec * 1.03) + 2.25)))
                + (0.05 * math.sin((elapsed_sec * 2.11) + 0.40))
            )
        left_wave = max(0.0, min(1.0, left_wave))
        right_wave = max(0.0, min(1.0, right_wave))
        left_pos = left_wave * (cycle - 1)
        right_pos = right_wave * (cycle - 1)

        out = Text()
        out.append("⟦", style=colors["edge"])
        for pos in range(cycle):
            left_distance = abs(pos - left_pos)
            right_distance = abs(pos - right_pos)
            distance = min(left_distance, right_distance)
            overlap = abs(left_pos - right_pos)
            center_emphasis = (
                stage == "linger"
                and overlap <= 0.55
                and abs(pos - ((cycle - 1) / 2.0)) <= 1.0
            )

            if overlap <= 0.28 and distance <= 0.42:
                style = colors["cell_peak"]
                char = "■"
            elif center_emphasis and distance <= 0.95:
                style = colors["cell_near"]
                char = "▪"
            elif distance <= 0.42:
                style = colors["cell_peak"]
                char = "■"
            elif distance <= 1.05:
                style = colors["cell_near"]
                char = "▪"
            elif distance <= 1.8:
                style = colors["cell_tail"]
                char = "·"
            else:
                style = colors["cell_dim"]
                char = "·"

            out.append(char, style=style)
        out.append("⟧", style=colors["edge"])
        return out

    @classmethod
    def _loop_motion_profile(cls, phase: float) -> dict[str, float | str]:
        spec             = cls.status_spec("loop")
        elapsed_sec      = phase / max(0.001, spec.phase_rate)
        sweep_period_sec = 3.0
        cycle_sec        = elapsed_sec % sweep_period_sec

        converge_sec = 0.42
        linger_sec   = 0.68
        release_sec  = 0.82
        idle_sec     = max(0.1, sweep_period_sec - converge_sec - linger_sec - release_sec)

        if cycle_sec < converge_sec:
            stage = "converge"
            stage_progress = cycle_sec / converge_sec
            motion = 0.5 * cls._smoothstep(stage_progress)
        elif cycle_sec < converge_sec + linger_sec:
            stage = "linger"
            stage_progress = (cycle_sec - converge_sec) / linger_sec
            breath = math.sin(stage_progress * math.tau * 1.15)
            motion = 0.5 + (0.045 * breath)
        elif cycle_sec < converge_sec + linger_sec + release_sec:
            stage = "release"
            stage_progress = (
                (cycle_sec - converge_sec - linger_sec) / release_sec
            )
            motion = 0.5 + (0.5 * cls._smoothstep(stage_progress))
        else:
            stage = "idle"
            stage_progress = (
                (cycle_sec - converge_sec - linger_sec - release_sec) / idle_sec
            )
            drift = 0.012 * math.sin(stage_progress * math.tau)
            motion = 0.988 + drift

        return {
            "elapsed_sec"    : elapsed_sec,
            "cycle_sec"      : cycle_sec,
            "stage"          : stage,
            "stage_progress" : max(0.0, min(1.0, stage_progress)),
            "motion"         : max(0.0, min(1.0, motion))
        }

    @classmethod
    def tool_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("tool")

        colors = {
            "edge"      : "bold #6A6256",
            "shell"     : "bold #B9AB96",
            "core"      : "bold #EDE2CE",
            "pulse"     : "bold #DCC8AB",
            "dust"      : "bold #857866",
            "text_peak" : "bold #E6D7BF",
            "text_soft" : "bold #DFD0B8",
            "text_near" : "bold #D7C7AF",
            "text_mid"  : "bold #C5B094",
            "text_fade" : "bold #AB967F",
            "text_dim"  : "bold #8C7B6A"
        }

        text = cls.fit_status_text(text, kind="tool", fallback="function calling")
        span = max(1, len(text))

        sweep = cls._sway_focus(
            phase,
            span,
            speed=spec.scan_speed,
            pad=spec.scan_pad
        )

        out = cls._tool_status_indicator(
            phase,
            breathe_freq=0.30,
            frame_rate=0.12
        )
        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_sweep_text(
            out,
            text,
            focus=sweep,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            lead_span=spec.lead_span,
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius,
            soft_ratio=0.22,
            near_ratio=spec.near_ratio,
            mid_ratio=spec.mid_ratio
        )
        return out

    @classmethod
    def tool_status_static_renderable(cls, text: str) -> Text:
        return cls.tool_status_renderable(0.0, text)

    @classmethod
    def code_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("code")

        colors = {
            "edge"      : "bold #3F4A5A",
            "spin"      : "bold #86A6C9",
            "spin_hot"  : "bold #D6E6F7",
            "text_peak" : "bold #DCE8F5",
            "text_soft" : "bold #BECFE3",
            "text_near" : "bold #9DB3CA",
            "text_mid"  : "bold #748CA6",
            "text_fade" : "bold #5B6F86",
            "text_dim"  : "bold #455565"
        }

        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        frame = frames[int(max(0.0, phase) * 0.95) % len(frames)]
        pulse = 0.5 + (0.5 * math.sin(phase * spec.shell_freq))

        out = Text()
        out.append(frame, style=colors["spin_hot"] if pulse > 0.58 else colors["spin"])
        out.append(cls.status_content_gap(), style=colors["edge"])

        text = cls.fit_status_text(text, kind="code", fallback="Running")
        span = max(1, len(text))
        focus = cls._sway_focus(
            phase,
            span,
            speed=spec.scan_speed,
            pad=spec.scan_pad
        )
        cls._append_sweep_text(
            out,
            text,
            focus=focus,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            lead_span=spec.lead_span,
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius,
            soft_ratio=0.20,
            near_ratio=spec.near_ratio,
            mid_ratio=spec.mid_ratio
        )
        return out

    @classmethod
    def code_status_static_renderable(cls, text: str) -> Text:
        return cls.code_status_renderable(0.0, text)

    @classmethod
    def loop_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("loop")

        colors = {
            "edge"      : "bold #465661",
            "text_peak" : "bold #E4FBFD",
            "text_soft" : "bold #D2F0F4",
            "text_near" : "bold #B0D6DE",
            "text_mid"  : "bold #84AAB3",
            "text_fade" : "bold #607881",
            "text_dim"  : "bold #4B5D65"
        }

        text = cls.fit_status_text(text, kind="loop", fallback="loop steps")
        out  = cls._loop_status_indicator(phase)
        out.append(cls.status_content_gap(), style=colors["edge"])

        span    = max(1, len(text))
        travel  = max(1.0, float(span - 1) + (spec.scan_pad * 2.0))
        profile = cls._loop_motion_profile(phase)

        lock_progress = float(profile["motion"])
        focus = (lock_progress * travel) - spec.scan_pad

        mirror_focus = max(0.0, float(span - 1) - focus)
        peak_radius  = spec.peak_radius
        near_span    = spec.lead_span
        fade_span    = spec.tail_span
        lock_radius  = 1.25
        pulse        = 0.0

        if profile["stage"] == "linger":
            pulse = 0.5 + (0.5 * math.sin(float(profile["stage_progress"]) * math.tau * 1.15))
        elif profile["stage"] == "idle":
            pulse = 0.5 + (0.5 * math.sin(float(profile["stage_progress"]) * math.tau))

        if pulse > 0.0:
            peak_radius += 0.10 * pulse
            near_span += 0.24 * pulse
            fade_span += 0.36 * pulse
            lock_radius += 0.22 * pulse
            if profile["stage"] == "idle":
                colors["text_peak"] = f"bold {mix_hex_color('#E4FBFD', '#7FE7F4', pulse)}"
                colors["text_soft"] = f"bold {mix_hex_color('#D2F0F4', '#D2F0F4', pulse)}"
                colors["text_near"] = f"bold {mix_hex_color('#B0D6DE', '#B0D6DE', pulse)}"
            else:
                colors["text_peak"] = f"bold {mix_hex_color('#E4FBFD', '#9FE8F2', pulse)}"
                colors["text_soft"] = f"bold {mix_hex_color('#D2F0F4', '#7FD9E6', pulse)}"
                colors["text_near"] = f"bold {mix_hex_color('#B0D6DE', '#66C4D4', pulse)}"
        cls._append_repair_text(
            out,
            text,
            left_focus=focus,
            right_focus=mirror_focus,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            near_span=near_span,
            fade_span=fade_span,
            peak_radius=peak_radius,
            lock_radius=lock_radius
        )
        return out

    @classmethod
    def loop_status_static_renderable(cls, text: str) -> Text:
        return cls.loop_status_renderable(0.0, text)

    @classmethod
    def builtin_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("builtin")

        status_shell_motion_scale    = 0.76
        status_inner_solid_threshold = 0.87
        status_inner_soft_threshold  = 0.70
        status_outer_solid_threshold = 0.92
        status_outer_soft_threshold  = 0.77
        status_edge_threshold        = 0.83

        stable_colors = {
            "edge"      : "bold #40515D",
            "core"      : "bold #DCE9ED",
            "near"      : "bold #B3CAD3",
            "trail"     : "bold #78949F",
            "dust"      : "bold #4C606B",
            "text"      : "bold #DCEAF0",
            "text_soft" : "bold #D2E3E9",
            "text_near" : "bold #C2D7DE",
            "text_mid"  : "bold #9DB8C2",
            "text_fade" : "bold #697F89",
            "text_dim"  : "bold #53656E"
        }

        text   = cls.fit_status_text(text, kind="builtin", fallback="working")

        colors            = stable_colors
        shell_motion      = phase * (spec.shell_freq * status_shell_motion_scale)
        breathe           = 0.5 + (0.5 * math.sin(shell_motion))
        left_outer_phase  = 0.5 + (0.5 * math.sin(shell_motion - 1.45))
        left_inner_phase  = 0.5 + (0.5 * math.sin(shell_motion - 0.75))
        right_inner_phase = 0.5 + (0.5 * math.sin(shell_motion + 0.75))
        right_outer_phase = 0.5 + (0.5 * math.sin(shell_motion + 1.45))
        shell_phase       = 0.5 + (0.5 * math.sin(shell_motion + 2.1))
        span              = max(1, len(text))

        drift = cls._drift_focus(
            phase,
            span,
            entry_pad=spec.entry_pad,
            exit_pad=spec.exit_pad
        )

        core = cls._status_core_char(
            breathe,
            peak="*",
            high="O",
            mid="o",
            low="."
        )
        left_inner = cls._status_shell_char(
            left_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_inner = cls._status_shell_char(
            right_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        left_outer = cls._status_shell_char(
            left_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        right_outer = cls._status_shell_char(
            right_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        edge_left, edge_right = cls._status_edge_pair(shell_phase, threshold=status_edge_threshold)

        out = cls._build_status_shell(
            edge_left=edge_left,
            edge_right=edge_right,
            left_outer=left_outer,
            left_inner=left_inner,
            core=core,
            right_inner=right_inner,
            right_outer=right_outer,
            edge_style=colors["edge"],
            outer_style=colors["trail"],
            inner_style=colors["near"],
            core_style=colors["core"]
        )
        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_gradient_sweep_text(
            out,
            text,
            focus=drift,
            peak_color="#EEF9FD",
            soft_color="#E5F3F8",
            near_color="#D7EDF5",
            mid_color="#B3CDD8",
            fade_color="#708894",
            dim_color="#566C78",
            lead_span=spec.lead_span,
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius
        )
        return out

    @classmethod
    def thinking_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("wait")

        status_shell_motion_scale    = 0.76
        status_inner_solid_threshold = 0.87
        status_inner_soft_threshold  = 0.70
        status_outer_solid_threshold = 0.92
        status_outer_soft_threshold  = 0.77
        status_edge_threshold        = 0.83

        colors = {
            "edge"      : "bold #445856",
            "dot"       : "bold #D7E6E1",
            "dot_soft"  : "bold #B5CAC4",
            "dot_dim"   : "bold #748A85",
            "text"      : "bold #DDE7E3",
            "text_near" : "bold #A4B5B0",
            "text_tail" : "bold #667873",
            "text_dim"  : "bold #465652"
        }

        text = cls.fit_status_text(text, kind="wait", fallback="thinking")

        shell_motion      = phase * (spec.shell_freq * status_shell_motion_scale)
        breathe           = 0.5 + (0.5 * math.sin(shell_motion))
        left_outer_phase  = 0.5 + (0.5 * math.sin(shell_motion - 1.7))
        left_inner_phase  = 0.5 + (0.5 * math.sin(shell_motion - 0.9))
        right_inner_phase = 0.5 + (0.5 * math.sin(shell_motion + 0.9))
        right_outer_phase = 0.5 + (0.5 * math.sin(shell_motion + 1.7))
        shell_phase       = 0.5 + (0.5 * math.sin(shell_motion + 2.2))

        left_outer = cls._status_shell_char(
            left_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        left_inner = cls._status_shell_char(
            left_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_inner = cls._status_shell_char(
            right_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_outer = cls._status_shell_char(
            right_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        core = cls._status_core_char(
            breathe,
            peak="*",
            high="O",
            mid="o",
            low="."
        )
        edge_left, edge_right = cls._status_edge_pair(shell_phase, threshold=status_edge_threshold)

        out = cls._build_status_shell(
            edge_left=edge_left,
            edge_right=edge_right,
            left_outer=left_outer,
            left_inner=left_inner,
            core=core,
            right_inner=right_inner,
            right_outer=right_outer,
            edge_style=colors["edge"],
            outer_style=colors["dot_dim"],
            inner_style=colors["dot_soft"],
            core_style=colors["dot"] if breathe > 0.60 else colors["dot_soft"]
        )
        out.append(cls.status_content_gap(), style=colors["edge"])

        span = max(1, len(text))
        head = (
            (phase * spec.head_speed) % max(1.0, float((span * 2) + spec.cycle_padding))
        ) + spec.head_offset
        if head < float(span - 1):
            tail = spec.tail_reset
        else:
            tail = head - max(0.0, float(span - 1))
        cls._append_progressive_text(
            out,
            text,
            head=head,
            tail=tail,
            peak_style=colors["text"],
            near_style=colors["text_near"],
            tail_style=colors["text_tail"],
            dim_style=colors["text_dim"],
            lead_glow=spec.lead_glow,
            tail_glow=spec.tail_glow
        )
        return out

    @classmethod
    def heal_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("heal")

        colors = {
            "edge"      : "bold #3C535B",
            "shell"     : "bold #58C2CF",
            "pulse"     : "bold #8CEAF4",
            "core"      : "bold #E2FCFF",
            "dust"      : "bold #486A71",
            "text_peak" : "bold #EEFCFF",
            "text_soft" : "bold #D7F7FB",
            "text_near" : "bold #B4E6ED",
            "text_mid"  : "bold #81B1B9",
            "text_fade" : "bold #5B7C83",
            "text_dim"  : "bold #455C62"
        }

        out = cls._heal_status_indicator(
            phase,
            pulse_freq=0.84,
            frame_rate=0.38
        )
        text = cls.fit_status_text(text, kind="heal", fallback="restoring signal")
        span = max(1, len(text))
        travel = max(1.0, float(span - 1) + (spec.scan_pad * 2.0))
        progress = 0.5 - (0.5 * math.cos(phase * spec.scan_speed))
        lock_progress = cls._smoothstep(progress)
        focus = (lock_progress * travel) - spec.scan_pad
        mirror_focus = max(0.0, float(span - 1) - focus)

        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_repair_text(
            out,
            text,
            left_focus=focus,
            right_focus=mirror_focus,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            near_span=spec.lead_span,
            fade_span=spec.tail_span,
            peak_radius=spec.peak_radius,
            lock_radius=1.35
        )
        return out


if __name__ == '__main__':
    pass
