# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth
from frontends.terminal.color_support import TerminalColorLevel

from frontends.tui.core.status_frames import (
    INDICATOR_BLINK_INTERVAL_SECONDS,
    SPINNER_FRAMES,
    SWEEP_BAND_HALF_WIDTH,
    SWEEP_FRAME_INTERVAL_SECONDS,
    SWEEP_PADDING,
    SWEEP_PERIOD_SECONDS,
    SWEEP_PROFILES,
    _character_cells,
    _display_span,
    _sweep_focus,
    _sweep_intensity,
    render_status_fragments,
    spinner_indicator_fragment,
    status_indicator_fragment,
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
        ("retry", "Retrying"),
        ("provider_retry", "Retrying"),
        ("wait", "Waiting for background terminal"),
    ),
)
def test_status_sweep_changes_text_colors_through_active_pass(
    family,
    text,
) -> None:
    interval = status_interval(family)
    frame_count = int(SWEEP_PERIOD_SECONDS / interval) + 1
    frames = [
        render_status_fragments(
            text,
            family=family,
            phase=index * interval,
            animated=True,
            color_level=TerminalColorLevel.TRUECOLOR,
        )
        for index in range(frame_count)
    ]
    text_styles = [
        tuple(style for style, _value in frame[2:])
        for frame in frames
    ]

    assert len(set(text_styles)) >= 2
    assert {
        style
        for frame_styles in text_styles
        for style in frame_styles
    } <= {
        "class:terminal.attention.plain",
        "class:terminal.attention.plain bold",
        "class:terminal.attention.plain dim",
        "class:terminal.accent",
        "class:terminal.accent bold",
        "class:terminal.accent dim",
        "class:terminal.brand",
        "class:terminal.brand bold",
        "class:terminal.brand dim",
    }
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
        "fg:" not in style
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
            status_indicator_fragment(
                index / 30,
                family=family,
                animated=True,
            )[1]
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
    assert status_indicator_fragment(
        INDICATOR_BLINK_INTERVAL_SECONDS - 0.001,
        family="wait",
        animated=True,
    )[1] == "•"
    assert status_indicator_fragment(
        INDICATOR_BLINK_INTERVAL_SECONDS,
        family="wait",
        animated=True,
    )[1] == "◦"
    assert status_indicator_fragment(
        INDICATOR_BLINK_INTERVAL_SECONDS * 2,
        family="wait",
        animated=True,
    )[1] == "•"


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
    frames = {
        spinner_indicator_fragment(index / 10)
        for index in range(len(SPINNER_FRAMES))
    }
    indicators = {glyph for _style, glyph in frames}

    assert indicators == set(SPINNER_FRAMES)
    assert all(get_cwidth(glyph) == 1 for glyph in indicators)
    assert all("bold" not in style and "dim" not in style for style, _glyph in frames)
    assert spinner_indicator_fragment(
        0.1,
        color_level=TerminalColorLevel.NONE,
    )[0] == ""


def test_status_sweep_uses_display_width_and_fixed_codex_period() -> None:
    short_span = _display_span(_character_cells("Thinking"))
    long_span = _display_span(_character_cells("waiting for external tool response"))
    wide_span = _display_span(_character_cells("处理中"))

    assert short_span == get_cwidth("Thinking")
    assert long_span == get_cwidth("waiting for external tool response")
    assert wide_span == get_cwidth("处理中") == 6

    sample_elapsed = SWEEP_PERIOD_SECONDS * 0.25
    short_step = _sweep_focus(
        sample_elapsed,
        span=short_span,
    ) - _sweep_focus(
        0.0,
        span=short_span,
    )
    long_step = _sweep_focus(
        sample_elapsed,
        span=long_span,
    ) - _sweep_focus(
        0.0,
        span=long_span,
    )

    assert 0.0 < short_step < long_step
    assert status_interval("tool") == SWEEP_FRAME_INTERVAL_SECONDS
    assert status_phase_rate("tool") == status_phase_rate("wait") == 1.0


def test_status_sweep_uses_codex_padding_and_restarts_after_two_seconds() -> None:
    for span in (get_cwidth("Thinking"), get_cwidth("处理中")):
        assert _sweep_focus(0.0, span=span) == -SWEEP_PADDING
        assert _sweep_focus(SWEEP_PERIOD_SECONDS, span=span) == -SWEEP_PADDING
        assert _sweep_focus(
            SWEEP_PERIOD_SECONDS - SWEEP_FRAME_INTERVAL_SECONDS,
            span=span,
        ) > span


def test_status_sweep_keeps_a_local_symmetric_glow_band() -> None:
    cells   = _character_cells("abcdefghijklmnop")
    span    = _display_span(cells)

    focus = _sweep_focus(
        SWEEP_PERIOD_SECONDS * 0.5,
        span=span,
    )
    intensities = [
        _sweep_intensity(
            position,
            focus=focus,
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
    ) == pytest.approx(_sweep_intensity(
        focus + 2.0,
        focus=focus,
    ))
    assert _sweep_intensity(
        focus + SWEEP_BAND_HALF_WIDTH,
        focus=focus,
    ) == 0.0


def test_limited_color_status_uses_intensity_modifiers() -> None:
    text    = "abcdefghijklmnop"
    span    = _display_span(_character_cells(text))
    phase   = SWEEP_PERIOD_SECONDS * 0.5

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


def test_status_semantic_role_is_stable_and_no_color_has_no_color_class() -> None:
    for family in SWEEP_PROFILES:
        static_start = render_status_fragments(
            "working",
            family=family,
            phase=0.0,
            animated=False,
        )
        static_later = render_status_fragments(
            "working",
            family=family,
            phase=60.0,
            animated=False,
        )
        no_color = render_status_fragments(
            "working",
            family=family,
            phase=0.0,
            animated=True,
            color_level=TerminalColorLevel.NONE,
        )

        assert static_start == static_later
        assert all("class:terminal." not in style for style, _text in no_color)
        assert all("fg:" not in style and "bg:" not in style for style, _text in no_color)
