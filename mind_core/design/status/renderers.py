# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from rich.text import Text
from rich.cells import cell_len
from .elapsed import (
    elapsed_format_key,
    format_elapsed,
    max_elapsed_display_width,
    pad_elapsed_label,
)
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
    def _append_forward_sweep_text(
        cls,
        out: Text,
        text: str,
        *,
        focus: float,
        peak_style: str,
        dim_style: str,
        soft_style: str,
        near_style: str,
        mid_style: str,
        fade_style: str,
        tail_span: float,
        peak_radius: float
    ) -> None:
        tail_span = max(0.001, float(tail_span))
        peak_radius = max(0.0, float(peak_radius))

        for pos, char in enumerate(text):
            delta = focus - pos
            if 0.0 <= delta <= peak_radius:
                style = peak_style
            elif delta > peak_radius:
                tail = (delta - peak_radius) / tail_span
                if tail <= 0.14:
                    style = soft_style
                elif tail <= 0.34:
                    style = near_style
                elif tail <= 0.62:
                    style = mid_style
                elif tail <= 1.0:
                    style = fade_style
                else:
                    style = dim_style
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
    def status_elapsed_renderable(cls, elapsed_sec: float) -> Text:
        elapsed = max(0.0, float(elapsed_sec or 0.0))
        key = elapsed_format_key(elapsed)
        label = pad_elapsed_label(
            format_elapsed(elapsed),
            key=key,
            width=max_elapsed_display_width()
        )

        out = Text()
        separator = cls.status_elapsed_separator()
        out.append(separator[:2], style="bold #435057")
        out.append(separator[2], style="bold #4C5A61")
        out.append(separator[3:], style="bold #435057")
        out.append(label, style="bold #5D696F")
        return out

    @staticmethod
    def mode_status_text(mode: typing.Any) -> str:
        normalized = str(mode or "").strip().lower()

        labels = {
            "chat" : "Chat Stream",
            "fast" : "Fast Stream",
            "xtra" : "Xtra Stream",
        }

        return labels.get(normalized, "Mind Stream")

    @classmethod
    def _breathing_status_dot(
        cls,
        phase: float,
        *,
        dim_color: str,
        peak_color: str,
        breathe_freq: float
    ) -> Text:
        breathe = 0.5 + (0.5 * math.sin(phase * breathe_freq))
        color   = mix_hex_color(dim_color, peak_color, cls._smoothstep(breathe) * 0.72)
        dot     = "•" if breathe > 0.58 else "◦"

        out = Text()
        out.append(dot, style=f"bold {color}")
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
        _ = subtle, frame_rate

        return cls._breathing_status_dot(
            phase,
            dim_color="#5A4B42",
            peak_color="#DCC8AB",
            breathe_freq=breathe_freq
        )

    @classmethod
    def _mode_status_indicator(cls, phase: float) -> Text:
        return cls._breathing_status_dot(
            phase,
            dim_color="#2F4C5A",
            peak_color="#91D7ED",
            breathe_freq=0.44
        )

    @classmethod
    def tool_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("tool")

        colors = {
            "edge"      : "bold #6A6256",
            "shell"     : "bold #B9AB96",
            "core"      : "bold #EDE2CE",
            "pulse"     : "bold #DCC8AB",
            "dust"      : "bold #857866",
            "text_peak" : "bold #FFF4D8",
            "text_soft" : "bold #F6DCA8",
            "text_near" : "bold #D8B77F",
            "text_mid"  : "bold #A48662",
            "text_fade" : "bold #755F4E",
            "text_dim"  : "bold #5A4B42"
        }

        text = cls.fit_status_text(text, kind="tool", fallback="function calling")
        span = max(1, len(text))

        focus = cls._drift_focus(
            phase,
            span,
            entry_pad=spec.entry_pad,
            exit_pad=spec.exit_pad
        )

        out = cls._tool_status_indicator(
            phase,
            breathe_freq=0.30,
            frame_rate=0.12
        )
        out.append(cls.status_content_gap(), style=colors["edge"])

        cls._append_forward_sweep_text(
            out,
            text,
            focus=focus,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius
        )
        return out

    @classmethod
    def mode_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("mode")

        colors = {
            "edge"      : "bold #3F505C",
            "text_peak" : "bold #EAF9FF",
            "text_soft" : "bold #C7EEF9",
            "text_near" : "bold #91D7ED",
            "text_mid"  : "bold #4B8FA8",
            "text_fade" : "bold #335F72",
            "text_dim"  : "bold #2F4C5A"
        }

        out = cls._mode_status_indicator(phase)
        out.append(cls.status_content_gap(), style=colors["edge"])

        text = cls.fit_status_text(text, kind="mode", fallback="Mind Stream")
        span = max(1, len(text))

        focus = cls._drift_focus(
            phase,
            span,
            entry_pad=spec.entry_pad,
            exit_pad=spec.exit_pad
        )

        cls._append_forward_sweep_text(
            out,
            text,
            focus=focus,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius
        )
        return out

    @classmethod
    def thinking_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("wait")

        status_shell_motion_scale = 0.52

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

        out = cls._breathing_status_dot(
            phase,
            dim_color="#465652",
            peak_color="#B5CAC4",
            breathe_freq=spec.shell_freq * status_shell_motion_scale
        )
        out.append(cls.status_content_gap(), style=colors["edge"])

        span = max(1, len(text.rstrip()) or len(text))

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


if __name__ == '__main__':
    pass
