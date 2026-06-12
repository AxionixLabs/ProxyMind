# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass


@dataclass(frozen=True)
class SweepStatusSpec(object):
    refresh_per_second: int
    phase_rate: float
    text_limit: int
    shell_freq: float
    lead_span: float
    tail_span: float
    peak_radius: float
    near_ratio: float
    mid_ratio: float
    scan_speed: float = 0.0
    scan_pad: float = 0.0
    drift_wobble_amp: float = 0.0
    drift_wobble_freq: float = 0.0
    drift_offset: float = 0.0
    entry_pad: float = 0.0
    exit_pad: float = 0.0
    label_width: int = 0


@dataclass(frozen=True)
class ProgressiveStatusSpec(object):
    refresh_per_second: int
    phase_rate: float
    text_limit: int
    shell_freq: float
    head_speed: float
    cycle_padding: float
    head_offset: float
    tail_reset: float
    lead_glow: float
    tail_glow: float


@dataclass(frozen=True)
class AgentLiveTheme(object):
    refresh_per_second: int
    text_width: int
    base_pad: str
    colors: dict[str, str]
    default_title: str
    default_detail: str


if __name__ == '__main__':
    pass
