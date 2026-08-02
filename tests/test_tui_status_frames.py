# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.status_frames import (
    SPINNER_FRAMES,
    SWEEP_PROFILES,
    _animated_palette,
    _character_cells,
    _display_span,
    _sweep_boundaries,
    _sweep_durations,
    _sweep_duration,
    _sweep_intensity,
    render_status_fragments,
    spinner_indicator_fragment,
    status_interval,
    status_phase_rate,
)


@pytest.mark.parametrize(
    ("family", "text"),
    (
        ("tool", "Function Calling"),
        ("wait", "Thinking"),
        ("tool", "运行"),
        ("wait", "处理中"),
    ),
)
def test_status_sweep_changes_text_colors_through_active_pass(
    family,
    text,
) -> None:
    interval = status_interval(family)
    frames = [
        render_status_fragments(
            text,
            family=family,
            phase=index * interval,
            animated=True,
        )
        for index in range(31)
    ]
    text_styles = [
        tuple(style for style, _value in frame[2:])
        for frame in frames
    ]

    assert len(set(text_styles)) >= 16
    assert {
        "".join(value for _style, value in frame)[1:]
        for frame in frames
    } == {f" {text}"}
    assert {
        frame[0][1]
        for frame in frames
    } == {"◦", "•"}
    assert all("bg:" not in style for frame in frames for style, _value in frame)
    assert all("bold" not in style for frame in frames for style, _value in frame)


def test_status_indicator_breathes_between_hollow_and_solid_glyphs() -> None:
    indicators = {
        family: {
            render_status_fragments(
                "working",
                family=family,
                phase=index / 30,
                animated=True,
            )[0][1]
            for index in range(60)
        }
        for family in ("tool", "wait")
    }

    assert indicators == {"tool": {"◦", "•"}, "wait": {"◦", "•"}}
    assert all(
        get_cwidth(glyph) == 1
        for family in indicators.values()
        for glyph in family
    )


def test_explicit_spinner_keeps_one_cell_and_rotates() -> None:
    indicators = {
        spinner_indicator_fragment(index / 10)[1]
        for index in range(len(SPINNER_FRAMES))
    }

    assert indicators == set(SPINNER_FRAMES)
    assert all(get_cwidth(glyph) == 1 for glyph in indicators)


def test_status_sweep_uses_display_width_and_adaptive_speed() -> None:
    short_span = _display_span(_character_cells("Thinking"))
    long_span = _display_span(_character_cells("waiting for external tool response"))
    wide_span = _display_span(_character_cells("处理中"))

    assert short_span == get_cwidth("Thinking")
    assert long_span == get_cwidth("waiting for external tool response")
    assert wide_span == get_cwidth("处理中") == 6
    assert _sweep_duration(short_span) < _sweep_duration(long_span)

    interval = status_interval("tool")
    tool_short_step = _sweep_boundaries(
        interval,
        span=short_span,
        profile=SWEEP_PROFILES["tool"],
    )[0] - _sweep_boundaries(
        0.0,
        span=short_span,
        profile=SWEEP_PROFILES["tool"],
    )[0]
    tool_long_step = _sweep_boundaries(
        interval,
        span=long_span,
        profile=SWEEP_PROFILES["tool"],
    )[0] - _sweep_boundaries(
        0.0,
        span=long_span,
        profile=SWEEP_PROFILES["tool"],
    )[0]
    wait_short_step = _sweep_boundaries(
        interval,
        span=short_span,
        profile=SWEEP_PROFILES["wait"],
    )[0] - _sweep_boundaries(
        0.0,
        span=short_span,
        profile=SWEEP_PROFILES["wait"],
    )[0]

    assert 0.0 < wait_short_step < tool_short_step < tool_long_step < 1.2
    assert SWEEP_PROFILES["wait"].tail_span > SWEEP_PROFILES["tool"].tail_span
    assert status_phase_rate("tool") == status_phase_rate("wait") == 1.0


def test_status_sweep_has_a_wide_band_and_quiet_interval() -> None:
    span = _display_span(_character_cells("Thinking"))

    for family in ("tool", "wait"):
        profile = SWEEP_PROFILES[family]
        fill_duration, tail_duration = _sweep_durations(span, profile)
        active_duration = fill_duration + tail_duration
        resting_edges   = _sweep_boundaries(
            active_duration + (profile.rest_duration * 0.25),
            span=span,
            profile=profile,
        )
        late_rest_edges = _sweep_boundaries(
            active_duration + (profile.rest_duration * 0.9),
            span=span,
            profile=profile,
        )

        assert profile.lead_span >= 2.3
        assert profile.tail_span >= 7.0
        assert profile.rest_duration > fill_duration
        assert resting_edges == late_rest_edges


def test_status_sweep_fully_lights_text_before_tail_begins() -> None:
    cells   = _character_cells("Thinking")
    span    = _display_span(cells)
    profile = SWEEP_PROFILES["wait"]

    fill_duration, tail_duration = _sweep_durations(span, profile)
    full_head, full_tail = _sweep_boundaries(
        fill_duration,
        span=span,
        profile=profile,
    )
    full_intensities = [
        _sweep_intensity(
            position,
            head=full_head,
            tail=full_tail,
            lead_span=profile.lead_span,
            tail_span=profile.tail_span,
        )
        for position, char in cells
        if not char.isspace()
    ]

    assert full_intensities == [1.0] * len(full_intensities)

    next_head, next_tail = _sweep_boundaries(
        fill_duration + status_interval("wait"),
        span=span,
        profile=profile,
    )
    assert next_tail > full_tail
    assert _sweep_intensity(
        cells[0][0],
        head=next_head,
        tail=next_tail,
        lead_span=profile.lead_span,
        tail_span=profile.tail_span,
    ) < 1.0

    tail_head, tail_edge = _sweep_boundaries(
        fill_duration + (tail_duration * 0.4),
        span=span,
        profile=profile,
    )
    first_position = cells[0][0]
    last_position  = cells[-1][0]

    assert _sweep_intensity(
        first_position,
        head=tail_head,
        tail=tail_edge,
        lead_span=profile.lead_span,
        tail_span=profile.tail_span,
    ) < 1.0
    assert _sweep_intensity(
        last_position,
        head=tail_head,
        tail=tail_edge,
        lead_span=profile.lead_span,
        tail_span=profile.tail_span,
    ) == 1.0


def test_status_palette_changes_smoothly_over_time() -> None:
    for family, profile in SWEEP_PROFILES.items():
        start = _animated_palette(profile, 0.0)
        middle = _animated_palette(profile, profile.palette_period / 2)
        next_palette = _animated_palette(profile, profile.palette_period)
        static_start = render_status_fragments(
            "working",
            family=family,
            phase=0.0,
            animated=False,
        )
        static_later = render_status_fragments(
            "working",
            family=family,
            phase=profile.palette_period,
            animated=False,
        )

        assert start.color_stops != middle.color_stops
        assert middle.color_stops != next_palette.color_stops
        assert next_palette == profile.palettes[1]
        assert static_start == static_later
