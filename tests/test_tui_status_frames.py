# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.terminal.capabilities import TerminalColorLevel

from frontends.tui.core.status_frames import (
    SPINNER_FRAMES,
    SWEEP_PROFILES,
    _animated_palette,
    _character_cells,
    _display_span,
    _sweep_duration,
    _sweep_focus,
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
            color_level=TerminalColorLevel.TRUECOLOR,
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
    assert all(
        "dim" not in style
        for frame in frames
        for style, value in frame[2:]
        if value.strip()
    )
    bold_positions = {
        tuple(
            index
            for index, (style, value) in enumerate(frame[2:])
            if value.strip() and "bold" in style
        )
        for frame in frames
    }
    assert len(bold_positions) >= 4
    assert any(bold_positions)
    visible_count = sum(not char.isspace() for char in text)
    assert any(
        len(positions) < visible_count
        for positions in bold_positions
    )


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


def test_retry_palette_keeps_wait_sweep_geometry() -> None:
    phase = 0.73
    thinking = render_status_fragments(
        "Thinking",
        family="wait",
        phase=phase,
        animated=True,
        color_level=TerminalColorLevel.TRUECOLOR,
    )
    retrying = render_status_fragments(
        "Retrying",
        family="retry",
        phase=phase,
        animated=True,
        color_level=TerminalColorLevel.TRUECOLOR,
    )

    thinking_emphasis = tuple("bold" in style for style, _text in thinking[2:])
    retrying_emphasis = tuple("bold" in style for style, _text in retrying[2:])

    assert get_cwidth("Thinking") == get_cwidth("Retrying")
    assert thinking_emphasis == retrying_emphasis
    assert tuple(style for style, _text in thinking) != tuple(
        style for style, _text in retrying
    )


def test_provider_retry_palette_keeps_geometry_and_uses_distinct_colors() -> None:
    phase = 0.73
    network_retry = render_status_fragments(
        "Retrying",
        family="retry",
        phase=phase,
        animated=True,
        color_level=TerminalColorLevel.TRUECOLOR,
    )
    provider_retry = render_status_fragments(
        "Retrying",
        family="provider_retry",
        phase=phase,
        animated=True,
        color_level=TerminalColorLevel.TRUECOLOR,
    )

    assert tuple("bold" in style for style, _text in network_retry[2:]) == tuple(
        "bold" in style for style, _text in provider_retry[2:]
    )
    assert tuple(style for style, _text in network_retry) != tuple(
        style for style, _text in provider_retry
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
    tool_short_step = _sweep_focus(
        interval,
        span=short_span,
        profile=SWEEP_PROFILES["tool"],
    ) - _sweep_focus(
        0.0,
        span=short_span,
        profile=SWEEP_PROFILES["tool"],
    )
    tool_long_step = _sweep_focus(
        interval,
        span=long_span,
        profile=SWEEP_PROFILES["tool"],
    ) - _sweep_focus(
        0.0,
        span=long_span,
        profile=SWEEP_PROFILES["tool"],
    )
    wait_short_step = _sweep_focus(
        interval,
        span=short_span,
        profile=SWEEP_PROFILES["wait"],
    ) - _sweep_focus(
        0.0,
        span=short_span,
        profile=SWEEP_PROFILES["wait"],
    )

    assert 0.0 < wait_short_step < tool_short_step < tool_long_step < 1.2
    assert SWEEP_PROFILES["wait"].glow_span > SWEEP_PROFILES["tool"].glow_span
    assert status_phase_rate("tool") == status_phase_rate("wait") == 1.0


def test_status_sweep_has_a_wide_band_and_quiet_interval() -> None:
    span = _display_span(_character_cells("Thinking"))

    for family in ("tool", "wait"):
        profile = SWEEP_PROFILES[family]
        active_duration = _sweep_duration(span) / profile.speed_factor
        resting_focus   = _sweep_focus(
            active_duration + (profile.rest_duration * 0.25),
            span=span,
            profile=profile,
        )
        late_rest_focus = _sweep_focus(
            active_duration + (profile.rest_duration * 0.9),
            span=span,
            profile=profile,
        )

        assert profile.peak_radius >= 0.7
        assert profile.glow_span >= 2.6
        assert active_duration + profile.rest_duration <= 2.1
        assert resting_focus == late_rest_focus


def test_status_sweep_keeps_a_local_symmetric_glow_band() -> None:
    cells   = _character_cells("abcdefghijklmnop")
    span    = _display_span(cells)
    profile = SWEEP_PROFILES["wait"]

    active_duration = _sweep_duration(span) / profile.speed_factor
    focus = _sweep_focus(
        active_duration * 0.5,
        span=span,
        profile=profile,
    )
    intensities = [
        _sweep_intensity(
            position,
            focus=focus,
            peak_radius=profile.peak_radius,
            glow_span=profile.glow_span,
        )
        for position, char in cells
        if not char.isspace()
    ]
    visible = [level for level in intensities if level >= 0.2]

    assert 5 <= len(visible) <= 7
    assert max(intensities) >= 0.95
    assert intensities[0] == intensities[-1] == 0.0
    assert _sweep_intensity(
        focus - 2.0,
        focus=focus,
        peak_radius=profile.peak_radius,
        glow_span=profile.glow_span,
    ) == pytest.approx(_sweep_intensity(
        focus + 2.0,
        focus=focus,
        peak_radius=profile.peak_radius,
        glow_span=profile.glow_span,
    ))


def test_limited_color_status_uses_intensity_modifiers() -> None:
    text    = "abcdefghijklmnop"
    span    = _display_span(_character_cells(text))
    profile = SWEEP_PROFILES["wait"]
    phase   = (_sweep_duration(span) / profile.speed_factor) * 0.5

    frame = render_status_fragments(
        text,
        family="wait",
        phase=phase,
        animated=True,
        color_level=TerminalColorLevel.ANSI16,
    )
    styles = [style for style, value in frame[2:] if value.strip()]

    assert any("dim" in style for style in styles)
    assert any("bold" in style for style in styles)
    assert any("dim" not in style and "bold" not in style for style in styles)


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
