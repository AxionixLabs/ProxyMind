# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from frontends.terminal.capabilities import (
    TerminalCapabilities,
    TerminalTheme,
)
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)

from frontends.tui.core.status_frames import (
    INDICATOR_BLINK_INTERVAL_SECONDS,
    SPINNER_FRAMES,
    SWEEP_BAND_HALF_WIDTH,
    SWEEP_FRAME_INTERVAL_SECONDS,
    SWEEP_PADDING,
    SWEEP_PERIOD_SECONDS,
    SWEEP_PROFILES,
    TOOL_BREATH_EXECUTION_SECONDS,
    TOOL_BREATH_REVIEW_SECONDS,
    ToolBreathingMotion,
    _character_cells,
    _display_span,
    _sweep_focus,
    _sweep_intensity,
    render_status_fragments,
    spinner_indicator_fragment,
    status_indicator_fragment,
    status_interval,
    status_phase_rate,
    tool_breathing_fragment,
)
from frontends.tui.core.styles import build_tui_application_style


@pytest.mark.parametrize("level", list(TerminalColorLevel))
def test_tool_breathing_keeps_exact_bullet_width_and_degrades_without_flicker(level):
    frames = [tool_breathing_fragment(index / 200, color_level=level) for index in range(201)]
    assert {glyph for _style, glyph in frames} == {"•"}
    assert {get_cwidth(glyph) for _style, glyph in frames} == {1}
    if level in {TerminalColorLevel.TRUECOLOR, TerminalColorLevel.ANSI256}:
        steps = [int(style.rsplit(".", 1)[1]) for style, _glyph in frames]
        assert len(set(steps)) >= 48
        assert steps[0] == steps[-1]
        assert max(abs(right - left) for left, right in zip(steps, steps[1:])) <= 3
        assert steps[:57] == sorted(steps[:57])
        assert steps[68:177] == sorted(steps[68:177], reverse=True)
    else:
        assert len(set(frames)) == 1
        if level is TerminalColorLevel.NONE:
            assert frames[0][0] == ""


def test_tool_breathing_handoff_preserves_phase_and_smoothly_changes_speed():
    motion = ToolBreathingMotion()
    motion.advance(TOOL_BREATH_EXECUTION_SECONDS, reviewing=False)
    assert motion.cycle == pytest.approx(0.12)
    before = motion.cycle
    motion.advance(0, reviewing=True)
    assert motion.cycle == before
    motion.advance(0.2, reviewing=True)
    assert motion.rate == pytest.approx(1 / TOOL_BREATH_REVIEW_SECONDS, abs=0.01)
    assert 0.2 / TOOL_BREATH_REVIEW_SECONDS < motion.cycle - before < 0.2 / TOOL_BREATH_EXECUTION_SECONDS
    whole = ToolBreathingMotion()
    divided = ToolBreathingMotion()
    whole.advance(0.32, reviewing=True)
    for _ in range(10):
        divided.advance(0.032, reviewing=True)
    assert whole.cycle == pytest.approx(divided.cycle)
    assert whole.rate == pytest.approx(divided.rate)


@pytest.mark.parametrize("background", [(17, 17, 17), (245, 245, 245), None])
@pytest.mark.parametrize("level", [TerminalColorLevel.TRUECOLOR, TerminalColorLevel.ANSI256])
def test_tool_breathing_palette_preserves_weight_and_visible_trough(background, level):
    capabilities = TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.UNKNOWN, "unknown"),
        color_support=TerminalColorSupport.fixed(level),
        theme=TerminalTheme(background=background),
    )
    empty = Style.from_dict({})
    style = build_tui_application_style(empty, empty, empty, capabilities=capabilities)
    attrs = [style.get_attrs_for_style_str(f"class:tool.breath.{index}") for index in range(64)]
    assert not any(attr.bold or attr.dim or attr.bgcolor for attr in attrs)
    assert len({attr.color for attr in attrs}) >= (40 if level is TerminalColorLevel.TRUECOLOR else 3)
    if background is not None:
        for attr in attrs:
            rgb = tuple(int(attr.color[index:index + 2], 16) for index in (0, 2, 4))
            assert max(abs(left - right) for left, right in zip(rgb, background)) >= 70


@pytest.mark.parametrize(
    ("family", "text"),
    (
        ("wait", "Thinking"),
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


def test_wait_and_retry_indicators_alternate_hollow_and_solid_glyphs() -> None:
    indicators = {
        family: {
            status_indicator_fragment(
                index / 30,
                family=family,
                animated=True,
            )[1]
            for index in range(60)
        }
        for family in ("retry", "wait")
    }

    assert indicators == {"retry": {"◦", "•"}, "wait": {"◦", "•"}}
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
    assert status_interval("wait") == SWEEP_FRAME_INTERVAL_SECONDS
    assert status_phase_rate("retry") == status_phase_rate("wait") == 1.0


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
