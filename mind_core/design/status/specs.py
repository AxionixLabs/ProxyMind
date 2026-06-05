# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .types import (
    ProgressiveStatusSpec, SweepStatusSpec
)

TOOL_STATUS_SPEC = SweepStatusSpec(
    refresh_per_second=40,
    phase_rate=14.4,
    text_limit=48,
    shell_freq=0.52,
    lead_span=3.1,
    tail_span=6.8,
    peak_radius=0.74,
    near_ratio=0.56,
    mid_ratio=0.90,
    scan_speed=0.2,
    scan_pad=2.6
)
CODE_STATUS_SPEC = SweepStatusSpec(
    refresh_per_second=28,
    phase_rate=12.8,
    text_limit=56,
    shell_freq=0.42,
    lead_span=2.8,
    tail_span=5.2,
    peak_radius=0.70,
    near_ratio=0.50,
    mid_ratio=0.84,
    scan_speed=0.24,
    scan_pad=2.0
)
MODE_STATUS_SPEC = SweepStatusSpec(
    refresh_per_second=30,
    phase_rate=13.6,
    text_limit=56,
    shell_freq=0.44,
    lead_span=2.8,
    tail_span=4.6,
    peak_radius=0.52,
    near_ratio=0.50,
    mid_ratio=0.84,
    scan_speed=0.22,
    scan_pad=2.0,
    entry_pad=1.0,
    exit_pad=3.0
)
BUILTIN_STATUS_SPEC = SweepStatusSpec(
    refresh_per_second=40,
    phase_rate=15.8,
    text_limit=48,
    shell_freq=0.55,
    lead_span=3.4,
    tail_span=6.6,
    peak_radius=0.78,
    near_ratio=0.46,
    mid_ratio=0.88,
    drift_wobble_amp=0.0,
    drift_wobble_freq=0.0,
    drift_offset=0.0,
    entry_pad=6.0,
    exit_pad=8.4
)
HEAL_STATUS_SPEC = SweepStatusSpec(
    refresh_per_second=34,
    phase_rate=16.8,
    text_limit=56,
    shell_freq=0.68,
    lead_span=3.0,
    tail_span=5.8,
    peak_radius=0.72,
    near_ratio=0.50,
    mid_ratio=0.86,
    scan_speed=0.28,
    scan_pad=2.2
)
LOOP_STATUS_SPEC = SweepStatusSpec(
    refresh_per_second=30,
    phase_rate=14.6,
    text_limit=52,
    shell_freq=0.48,
    lead_span=2.8,
    tail_span=4.6,
    peak_radius=0.72,
    near_ratio=0.44,
    mid_ratio=0.80,
    scan_speed=0.15,
    scan_pad=1.8
)
WAIT_STATUS_SPEC = ProgressiveStatusSpec(
    refresh_per_second=24,
    phase_rate=15.6,
    text_limit=48,
    shell_freq=0.48,
    head_speed=0.78,
    cycle_padding=4.0,
    head_offset=-1.2,
    tail_reset=-1.6,
    lead_glow=0.36,
    tail_glow=1.0
)
STATUS_SPECS: dict[str, SweepStatusSpec | ProgressiveStatusSpec] = {
    "tool"    : TOOL_STATUS_SPEC,
    "code"    : CODE_STATUS_SPEC,
    "mode"    : MODE_STATUS_SPEC,
    "heal"    : HEAL_STATUS_SPEC,
    "loop"    : LOOP_STATUS_SPEC,
    "wait"    : WAIT_STATUS_SPEC,
    "builtin" : BUILTIN_STATUS_SPEC
}

STATUS_TEXT_CHROME_WIDTH: dict[str, int] = {
    "tool"    : 18,
    "code"    : 18,
    "mode"    : 18,
    "heal"    : 18,
    "loop"    : 18,
    "wait"    : 18,
    "builtin" : 18
}

STATUS_TEXT_FLOOR: dict[str, int] = {
    "tool"    : 16,
    "code"    : 16,
    "mode"    : 16,
    "heal"    : 16,
    "loop"    : 16,
    "wait"    : 16,
    "builtin" : 16
}


class StatusSpec(object):

    console: typing.Any | None = None

    @classmethod
    def status_spec(cls, kind: str) -> SweepStatusSpec | ProgressiveStatusSpec:
        return STATUS_SPECS.get(kind, BUILTIN_STATUS_SPEC)

    @classmethod
    def status_text_limit(cls, kind: str) -> int:
        console_width = 80
        if cls.console is not None:
            console_width = max(24, int(cls.console.width))

        spec = cls.status_spec(kind)
        chrome_width = STATUS_TEXT_CHROME_WIDTH.get(kind, STATUS_TEXT_CHROME_WIDTH["builtin"])
        visible_limit = max(12, console_width - chrome_width)
        return min(spec.text_limit, visible_limit)

    @staticmethod
    def status_text_floor(kind: str) -> int:
        return STATUS_TEXT_FLOOR.get(kind, STATUS_TEXT_FLOOR["builtin"])

    @classmethod
    def status_refresh_per_second(cls, kind: str) -> int:
        return int(cls.status_spec(kind).refresh_per_second)

    @classmethod
    def status_interval(cls, kind: str) -> float:
        return 1 / cls.status_refresh_per_second(kind)

    @classmethod
    def status_phase_rate(cls, kind: str) -> float:
        return float(cls.status_spec(kind).phase_rate)

    @classmethod
    def status_step(cls, kind: str) -> float:
        return cls.status_phase_rate(kind)


if __name__ == '__main__':
    pass
