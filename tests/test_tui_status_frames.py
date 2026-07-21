# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.status_frames import (
    SPINNER_FRAMES,
    SWEEP_PROFILES,
    _character_cells,
    _display_span,
    _sweep_duration,
    _sweep_focus,
    render_status_fragments,
    spinner_indicator_fragment,
    status_interval,
    status_phase_rate,
)


@pytest.mark.parametrize(
    ("family", "text"),
    (
        ("tool", "function calling"),
        ("wait", "thinking"),
        ("tool", "运行"),
        ("wait", "处理中"),
    ),
)
def test_status_sweep_changes_text_colors_on_every_frame(
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

    assert all(left != right for left, right in zip(text_styles, text_styles[1:]))
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
    short_span = _display_span(_character_cells("thinking"))
    long_span = _display_span(_character_cells("waiting for external tool response"))
    wide_span = _display_span(_character_cells("处理中"))

    assert short_span == get_cwidth("thinking")
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

    assert 0.0 < wait_short_step < tool_short_step < tool_long_step < 1.0
    assert status_phase_rate("tool") == status_phase_rate("wait") == 1.0
