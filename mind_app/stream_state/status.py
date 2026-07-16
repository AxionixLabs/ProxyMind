# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
from rich.text import Text, Span
from mind_core.design import Design
from mind_core.design.utils import mix_hex_color
from mind_core.design.status.elapsed import (
    format_elapsed,
    elapsed_format_key,
    elapsed_display_width,
    max_elapsed_display_width,
    pad_elapsed_label
)

StatusFamily = typing.Literal[
    "builtin",
    "tool",
    "code",
    "mode",
    "wait",
    "heal",
    "loop",
]


class StatusState(object):
    """管理状态行的渲染族与动画配置。"""

    FAMILY_BUILTIN: typing.Final[StatusFamily] = "builtin"
    FAMILY_TOOL: typing.Final[StatusFamily]    = "tool"
    FAMILY_CODE: typing.Final[StatusFamily]    = "code"
    FAMILY_MODE: typing.Final[StatusFamily]    = "mode"
    FAMILY_WAIT: typing.Final[StatusFamily]    = "wait"
    FAMILY_HEAL: typing.Final[StatusFamily]    = "heal"
    FAMILY_LOOP: typing.Final[StatusFamily]    = "loop"

    ELAPSED_VISIBLE_AFTER_SEC: typing.Final[float] = 0.65
    ELAPSED_FADE_IN_SEC: typing.Final[float]       = 0.16
    ELAPSED_TRANSITION_SEC: typing.Final[float]    = 0.18
    ELAPSED_SEPARATOR_DIM: typing.Final[str]       = "#435057"
    ELAPSED_SEPARATOR_DOT: typing.Final[str]       = "#4C5A61"
    ELAPSED_LABEL_DIM: typing.Final[str]           = "#5D696F"

    PRESENCE_FLOOR_HEX: typing.Final[str] = "#303A41"

    HEX_COLOR_PATTERN: typing.Final[typing.Pattern[str]] = re.compile(r"#[0-9A-Fa-f]{6}")

    def __init__(self) -> None:
        """初始化状态内容和动画过渡数据。"""
        self.text: str            = ""
        self.family: StatusFamily = self.FAMILY_BUILTIN
        self.phase: float         = 0.0
        self.animated: bool       = True
        self.started_at: float    = 0.0

        self._elapsed_label: str                = ""
        self._elapsed_key: str                  = ""
        self._elapsed_prev_label: str           = ""
        self._elapsed_prev_key: str             = ""
        self._elapsed_transition_started: float = 0.0

    @property
    def active(self) -> bool:
        """返回当前是否存在状态内容。"""
        return bool(self.text)

    @property
    def visible(self) -> bool:
        """返回当前状态是否可见。"""
        return self.active

    @property
    def animating(self) -> bool:
        """返回当前状态是否处于动画展示阶段。"""
        return self.active

    def set_status(
        self,
        text: typing.Optional[str],
        *,
        family: StatusFamily = FAMILY_BUILTIN,
        animated: bool = True,
        reset_phase_on_text_change: bool = True
    ) -> bool:
        """更新状态内容和渲染配置，并返回是否重置动画阶段。"""
        status = Design.truncate_status_text(
            text,
            limit=Design.status_text_limit(family)
        )
        if not status:
            return self.clear_status()

        next_family   = family
        next_animated = bool(animated)
        text_changed  = status != self.text

        config_changed = (
            next_family != self.family
            or next_animated != self.animated
        )
        reset_phase = (
            config_changed
            or (text_changed and reset_phase_on_text_change)
        )
        if reset_phase:
            self.phase = 0.0
            self.started_at = time.perf_counter()

        self._reset_elapsed_transition()

        self.text     = status
        self.family   = next_family
        self.animated = next_animated

        return reset_phase

    def clear_status(self) -> bool:
        """清空当前状态，并返回清空前是否可见。"""
        was_visible = self.active

        self.text       = ""
        self.family     = self.FAMILY_BUILTIN
        self.phase      = 0.0
        self.animated   = True
        self.started_at = 0.0

        self._reset_elapsed_transition()
        return was_visible

    def reset(self) -> None:
        """立即清空状态。"""
        self.text       = ""
        self.family     = self.FAMILY_BUILTIN
        self.phase      = 0.0
        self.animated   = True
        self.started_at = 0.0

        self._reset_elapsed_transition()

    def set_phase(self, phase: float) -> None:
        """更新活动状态的动画阶段值。"""
        if self.active:
            self.phase = float(phase or 0.0)

    def renderable(self) -> Text:
        """生成当前状态行的可渲染文本。"""
        if not self.active:
            return Text()

        out = self._family_renderable(
            self.family,
            0.0 if not self.animated else self.phase,
            self.text
        )
        self._append_elapsed(out)
        return out

    def refresh_per_second(self) -> int:
        """返回当前状态族每秒的刷新次数。"""
        if not self.active:
            return 4
        return int(Design.status_refresh_per_second(self.family))

    def interval(self) -> float:
        """返回当前状态族的刷新间隔。"""
        if not self.active:
            return 0.25
        return float(Design.status_interval(self.family))

    def step(self) -> float:
        """返回当前状态族每次刷新的动画步长。"""
        if not self.active:
            return 0.0
        return float(Design.status_step(self.family))

    def phase_rate(self) -> float:
        """返回当前状态族的动画阶段速率。"""
        if not self.active:
            return 0.0
        return float(Design.status_phase_rate(self.family))

    def _append_elapsed(
        self,
        out: Text
    ) -> None:
        """向状态行追加经过渡处理的耗时文本。"""
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

        label     = format_elapsed(max(0.0, float(elapsed)))
        label_key = elapsed_format_key(elapsed)

        label, label_key, label_presence = self._elapsed_display_state(label, label_key)

        width = max_elapsed_display_width()

        if self._elapsed_transition_started and self._elapsed_prev_key:
            width = max(width, elapsed_display_width(self._elapsed_prev_key))

        label     = pad_elapsed_label(label, key=label_key, width=width)
        aux       = Text()
        separator = Design.status_elapsed_separator()

        aux.append(separator[:2], style=self.ELAPSED_SEPARATOR_DIM)
        aux.append(separator[2], style=self.ELAPSED_SEPARATOR_DOT)
        aux.append(separator[3:], style=self.ELAPSED_SEPARATOR_DIM)
        aux.append(label, style=self.ELAPSED_LABEL_DIM)
        self._apply_presence(aux, label_presence * appearance)
        out.append_text(aux)

    @classmethod
    def _family_renderable(cls, family: StatusFamily, phase: float, text: str) -> Text:
        """按状态族生成一致布局的状态行主体。"""
        if family == cls.FAMILY_TOOL:
            return Design.tool_status_renderable(phase, text)
        if family == cls.FAMILY_CODE:
            return Design.code_status_renderable(phase, text)
        if family == cls.FAMILY_MODE:
            return Design.mode_status_renderable(phase, text)
        if family == cls.FAMILY_LOOP:
            return Design.loop_status_renderable(phase, text)
        if family == cls.FAMILY_HEAL:
            return Design.heal_status_renderable(phase, text)
        if family == cls.FAMILY_WAIT:
            return Design.thinking_status_renderable(phase, text)

        return Design.builtin_status_renderable(phase, text)

    @staticmethod
    def _elapsed_slot() -> Text:
        """生成用于稳定状态行宽度的耗时占位文本。"""
        slot_width = len(Design.status_elapsed_separator()) + max_elapsed_display_width()
        return Text(" " * slot_width)

    def _reset_elapsed_transition(self) -> None:
        """重置耗时标签的过渡状态。"""
        self._elapsed_label              = ""
        self._elapsed_key                = ""
        self._elapsed_prev_label         = ""
        self._elapsed_prev_key           = ""
        self._elapsed_transition_started = 0.0

    def _elapsed_display_state(self, label: str, label_key: str) -> tuple[str, str, float]:
        """计算耗时标签切换期间的显示内容和存在度。"""
        now = time.perf_counter()

        if not self._elapsed_label:
            self._elapsed_label = label
            self._elapsed_key   = label_key

            return label, label_key, 1.0

        if label_key != self._elapsed_key and label != self._elapsed_label:
            self._elapsed_prev_label         = self._elapsed_label
            self._elapsed_prev_key           = self._elapsed_key
            self._elapsed_label              = label
            self._elapsed_key                = label_key
            self._elapsed_transition_started = now

        if not self._elapsed_transition_started:
            self._elapsed_label = label
            self._elapsed_key   = label_key

            return label, label_key, 1.0

        age  = max(0.0, now - self._elapsed_transition_started)
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

        self._elapsed_prev_label         = ""
        self._elapsed_prev_key           = ""
        self._elapsed_transition_started = 0.0

        return self._elapsed_label, self._elapsed_key, 1.0

    def _elapsed_visibility(self, elapsed: float) -> float:
        """计算耗时标签在淡入阶段的可见度。"""
        fade_span = max(0.001, self.ELAPSED_FADE_IN_SEC)
        progress  = (max(0.0, float(elapsed)) - self.ELAPSED_VISIBLE_AFTER_SEC) / fade_span

        return self._smoothstep(progress)

    @staticmethod
    def _smoothstep(value: float) -> float:
        """将输入值映射为平滑的零到一过渡值。"""
        clamped = max(0.0, min(1.0, float(value)))
        return clamped * clamped * (3.0 - (2.0 * clamped))

    @classmethod
    def _fade_style_text(cls, style: str, presence: float) -> str:
        """按存在度调整样式文本中的十六进制颜色。"""
        weight = cls._smoothstep(presence)

        def replace(match: re.Match[str]) -> str:
            """按权重混合单个十六进制颜色。"""
            return mix_hex_color(
                cls.PRESENCE_FLOOR_HEX,
                match.group(0),
                weight
            )

        return cls.HEX_COLOR_PATTERN.sub(replace, style)

    @classmethod
    def _apply_presence(cls, out: Text, presence: float) -> None:
        """将存在度应用到文本及其局部样式。"""
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
