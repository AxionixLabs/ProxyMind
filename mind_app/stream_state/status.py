# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import re
import typing
from rich.text import Text, Span
from mind_core.design import Design, mix_hex_color
from .format_time import (
    format_elapsed,
    elapsed_format_key,
    elapsed_display_width,
    max_elapsed_display_width,
    pad_elapsed_label,
)

StatusFamily = typing.Literal[
    "builtin",
    "tool",
    "wait",
]


class StatusState(object):
    """管理状态行的渲染族与动画配置。"""

    FAMILY_BUILTIN: typing.Final[StatusFamily] = "builtin"
    FAMILY_TOOL: typing.Final[StatusFamily] = "tool"
    FAMILY_WAIT: typing.Final[StatusFamily] = "wait"

    ENTER_DURATION_SEC: typing.Final[float] = 0.14
    EXIT_DURATION_SEC: typing.Final[float] = 0.18
    EXIT_REFRESH_PER_SECOND: typing.Final[int] = 30
    ELAPSED_VISIBLE_AFTER_SEC: typing.Final[float] = 0.65
    ELAPSED_FADE_IN_SEC: typing.Final[float] = 0.16
    ELAPSED_TRANSITION_SEC: typing.Final[float] = 0.18
    ELAPSED_SEPARATOR_DIM: typing.Final[str] = "#435057"
    ELAPSED_SEPARATOR_DOT: typing.Final[str] = "#4C5A61"
    ELAPSED_LABEL_DIM: typing.Final[str] = "#5D696F"
    PRESENCE_FLOOR_HEX: typing.Final[str] = "#303A41"
    HEX_COLOR_PATTERN: typing.Final[typing.Pattern[str]] = re.compile(r"#[0-9A-Fa-f]{6}")

    def __init__(
        self
    ) -> None:
        self.text: str = ""
        self.family: StatusFamily = self.FAMILY_BUILTIN
        self.phase: float = 0.0
        self.animated: bool = True
        self.started_at: float = 0.0

        self._exit_text: str = ""
        self._exit_family: StatusFamily = self.FAMILY_BUILTIN
        self._exit_phase: float = 0.0
        self._exit_animated: bool = True
        self._exit_started_at: float = 0.0
        self._exit_elapsed: float = 0.0

        self._elapsed_label: str = ""
        self._elapsed_key: str = ""
        self._elapsed_prev_label: str = ""
        self._elapsed_prev_key: str = ""
        self._elapsed_transition_started: float = 0.0

    @property
    def active(self) -> bool:
        return bool(self.text)

    @property
    def visible(self) -> bool:
        self._prune_exit()
        return self.active or bool(self._exit_text)

    @property
    def animating(self) -> bool:
        self._prune_exit()
        return self.active or bool(self._exit_text)

    def set_status(
        self,
        text: typing.Optional[str],
        *,
        family: StatusFamily = FAMILY_BUILTIN,
        animated: bool = True
    ) -> bool:
        status = Design.truncate_status_text(
            text,
            limit=Design.status_text_limit(family)
        )
        if not status:
            return self.clear_status()

        next_family = family
        next_animated = bool(animated)
        reset_phase = (
            status != self.text
            or next_family != self.family
            or next_animated != self.animated
        )
        if reset_phase:
            self.phase = 0.0
            self.started_at = time.perf_counter()

        self._clear_exit()
        self._reset_elapsed_transition()
        self.text = status
        self.family = next_family
        self.animated = next_animated
        return reset_phase

    def clear_status(self) -> bool:
        was_visible = self.visible

        if self.active:
            now = time.perf_counter()
            self._exit_text = self.text
            self._exit_family = self.family
            self._exit_phase = self.phase
            self._exit_animated = self.animated
            self._exit_started_at = now
            self._exit_elapsed = max(0.0, now - self.started_at) if self.started_at else 0.0
        else:
            self._clear_exit()

        self.text = ""
        self.family = self.FAMILY_BUILTIN
        self.phase = 0.0
        self.animated = True
        self.started_at = 0.0
        self._reset_elapsed_transition()
        return was_visible

    def set_phase(self, phase: float) -> None:
        self._prune_exit()
        if self.active:
            self.phase = float(phase or 0.0)

    def renderable(self) -> Text:
        state = self._current_state()
        if state is None:
            return Text()

        text, family, phase, animated, presence, elapsed_override = state

        if not animated and family == self.FAMILY_TOOL:
            out = Design.tool_status_static_renderable(text)
        elif not animated:
            out = Text(text, style="bold #8FA4B8")
        elif family == self.FAMILY_TOOL:
            out = Design.tool_status_renderable(phase, text)
        elif family == self.FAMILY_WAIT:
            out = Design.thinking_status_renderable(phase, text)
        else:
            out = Design.builtin_status_renderable(phase, text)

        self._apply_presence(out, presence)
        self._append_elapsed(
            out,
            elapsed_override=elapsed_override,
            presence=presence
        )
        return out

    def refresh_per_second(self) -> int:
        state = self._current_state()
        if state is None:
            return 4

        _, family, _, animated, _, _ = state
        if self.active and animated:
            return int(Design.status_refresh_per_second(family))
        return self.EXIT_REFRESH_PER_SECOND

    def interval(self) -> float:
        state = self._current_state()
        if state is None:
            return 0.25

        _, family, _, animated, _, _ = state
        if self.active and animated:
            return float(Design.status_interval(family))
        return 1 / self.EXIT_REFRESH_PER_SECOND

    def step(self) -> float:
        if not self.active:
            return 0.0
        return float(Design.status_step(self.family))

    def phase_rate(self) -> float:
        if not self.active:
            return 0.0
        return float(Design.status_phase_rate(self.family))

    def _current_state(
        self
    ) -> typing.Optional[tuple[str, StatusFamily, float, bool, float, typing.Optional[float]]]:
        self._prune_exit()
        if self.active:
            age = max(0.0, time.perf_counter() - self.started_at)
            progress = min(1.0, age / self.ENTER_DURATION_SEC)
            presence = self._smoothstep(progress)
            return self.text, self.family, self.phase, self.animated, presence, None

        if self._exit_text:
            age = max(0.0, time.perf_counter() - self._exit_started_at)
            progress = min(1.0, age / self.EXIT_DURATION_SEC)
            presence = 1.0 - self._smoothstep(progress)
            return (
                self._exit_text,
                self._exit_family,
                self._exit_phase,
                self._exit_animated,
                presence,
                self._exit_elapsed
            )
        return None

    def _append_elapsed(
        self,
        out: Text,
        *,
        elapsed_override: typing.Optional[float],
        presence: float
    ) -> None:
        elapsed = elapsed_override
        if elapsed is None:
            if not self.started_at:
                out.append_text(self._elapsed_slot())
                return None
            elapsed = max(0.0, time.perf_counter() - self.started_at)
        if elapsed < self.ELAPSED_VISIBLE_AFTER_SEC:
            out.append_text(self._elapsed_slot())
            return None

        appearance = self._elapsed_visibility(elapsed)
        if appearance <= 0.0:
            out.append_text(self._elapsed_slot())
            return None

        label = format_elapsed(max(0.0, float(elapsed)))
        label_key = elapsed_format_key(elapsed)
        label, label_key, label_presence = self._elapsed_display_state(label, label_key)
        width = max_elapsed_display_width()
        if self._elapsed_transition_started and self._elapsed_prev_key:
            width = max(width, elapsed_display_width(self._elapsed_prev_key))
        label = pad_elapsed_label(label, key=label_key, width=width)
        aux = Text()
        separator = Design.status_elapsed_separator()
        aux.append(separator[:2], style=self.ELAPSED_SEPARATOR_DIM)
        aux.append(separator[2], style=self.ELAPSED_SEPARATOR_DOT)
        aux.append(separator[3:], style=self.ELAPSED_SEPARATOR_DIM)
        aux.append(label, style=self.ELAPSED_LABEL_DIM)
        self._apply_presence(aux, presence * label_presence * appearance)
        out.append_text(aux)

    @staticmethod
    def _elapsed_slot() -> Text:
        slot_width = len(Design.status_elapsed_separator()) + max_elapsed_display_width()
        return Text(" " * slot_width)

    def _prune_exit(self) -> None:
        if not self._exit_text:
            return None
        age = max(0.0, time.perf_counter() - self._exit_started_at)
        if age >= self.EXIT_DURATION_SEC:
            self._clear_exit()

    def _clear_exit(self) -> None:
        self._exit_text = ""
        self._exit_family = self.FAMILY_BUILTIN
        self._exit_phase = 0.0
        self._exit_animated = True
        self._exit_started_at = 0.0
        self._exit_elapsed = 0.0

    def _reset_elapsed_transition(self) -> None:
        self._elapsed_label = ""
        self._elapsed_key = ""
        self._elapsed_prev_label = ""
        self._elapsed_prev_key = ""
        self._elapsed_transition_started = 0.0

    def _elapsed_display_state(self, label: str, label_key: str) -> tuple[str, str, float]:
        now = time.perf_counter()

        if not self._elapsed_label:
            self._elapsed_label = label
            self._elapsed_key = label_key
            return label, label_key, 1.0

        if label_key != self._elapsed_key and label != self._elapsed_label:
            self._elapsed_prev_label = self._elapsed_label
            self._elapsed_prev_key = self._elapsed_key
            self._elapsed_label = label
            self._elapsed_key = label_key
            self._elapsed_transition_started = now

        if not self._elapsed_transition_started:
            self._elapsed_label = label
            self._elapsed_key = label_key
            return label, label_key, 1.0

        age = max(0.0, now - self._elapsed_transition_started)
        half = self.ELAPSED_TRANSITION_SEC / 2
        if age < half:
            progress = 1.0 - self._smoothstep(age / max(0.001, half))
            return (
                self._elapsed_prev_label or self._elapsed_label,
                self._elapsed_prev_key or self._elapsed_key,
                progress
            )
        if age < self.ELAPSED_TRANSITION_SEC:
            progress = self._smoothstep((age - half) / max(0.001, half))
            return self._elapsed_label, self._elapsed_key, progress

        self._elapsed_prev_label = ""
        self._elapsed_prev_key = ""
        self._elapsed_transition_started = 0.0
        return self._elapsed_label, self._elapsed_key, 1.0

    def _elapsed_visibility(self, elapsed: float) -> float:
        fade_span = max(0.001, self.ELAPSED_FADE_IN_SEC)
        progress = (max(0.0, float(elapsed)) - self.ELAPSED_VISIBLE_AFTER_SEC) / fade_span
        return self._smoothstep(progress)

    @staticmethod
    def _smoothstep(value: float) -> float:
        clamped = max(0.0, min(1.0, float(value)))
        return clamped * clamped * (3.0 - (2.0 * clamped))

    @classmethod
    def _fade_style_text(cls, style: str, presence: float) -> str:
        weight = cls._smoothstep(presence)

        def replace(match: re.Match[str]) -> str:
            return mix_hex_color(
                cls.PRESENCE_FLOOR_HEX,
                match.group(0),
                weight
            )

        return cls.HEX_COLOR_PATTERN.sub(replace, style)

    @classmethod
    def _apply_presence(cls, out: Text, presence: float) -> None:
        if out.style:
            out.style = cls._fade_style_text(str(out.style), presence)

        if not out.spans:
            return None

        faded_spans: list[Span] = []
        for span in out.spans:
            if span.style:
                faded_spans.append(
                    Span(
                        span.start,
                        span.end,
                        cls._fade_style_text(str(span.style), presence)
                    )
                )
                continue
            faded_spans.append(span)
        out.spans = faded_spans


if __name__ == '__main__':
    pass
