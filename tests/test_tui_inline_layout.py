# -*- coding: utf-8 -*-

from mind_app.tui.core.inline_layout import InlineLayoutState


def test_auxiliary_growth_is_reported_only_above_observed_height() -> None:
    state = InlineLayoutState(canvas_height_floor=7)

    assert state.auxiliary_growth_active(3)

    state.observe_auxiliary_layout(
        height=3,
        queued_height=3,
        natural_height=10,
    )

    assert state.auxiliary_height == 3
    assert state.queued_height == 3
    assert not state.auxiliary_growth_active(3)
    assert state.auxiliary_growth_active(4)


def test_queue_reduction_caps_canvas_without_settling_activity_height() -> None:
    state = InlineLayoutState(
        canvas_height_floor=13,
        auxiliary_height=6,
        queued_height=3,
    )

    state.observe_auxiliary_layout(
        height=4,
        queued_height=3,
        natural_height=10,
    )
    assert state.canvas_height_floor == 13

    state.observe_auxiliary_layout(
        height=1,
        queued_height=0,
        natural_height=7,
    )
    assert state.canvas_height_floor == 7

    state.reset()
    assert state.auxiliary_height == 0
    assert state.queued_height == 0


def test_single_line_input_does_not_start_growth_settlement() -> None:
    state = InlineLayoutState(canvas_height_floor=8)

    state.observe_input_layout(
        input_height=1,
        footprint_height=4,
        stable_line_baseline=0,
        live_height_baseline=0,
    )

    assert state.input_growth_active is False
    assert state.input_growth_baseline is None
    assert state.input_anchor.footprint_height == 0


def test_nonempty_input_settles_no_lower_than_growth_baseline() -> None:
    state = InlineLayoutState(canvas_height_floor=8)
    state.observe_input_layout(
        input_height=6,
        footprint_height=9,
        stable_line_baseline=0,
        live_height_baseline=0,
    )
    state.fit_canvas_height(natural_height=15, available_height=24)

    state.settle_input(natural_height=5, input_empty=False)

    assert state.canvas_height_floor == 8
    assert state.input_growth_baseline == 8


def test_empty_input_releases_every_expanded_row_below_footer() -> None:
    state = InlineLayoutState(canvas_height_floor=8)
    state.observe_input_layout(
        input_height=21,
        footprint_height=24,
        stable_line_baseline=4,
        live_height_baseline=2,
    )
    state.fit_canvas_height(natural_height=32, available_height=24)
    state.bottom_anchor.begin(
        footprint_height=10,
        completion_visible=True,
        stable_line_baseline=4,
        live_height_baseline=2,
    )

    state.settle_input(natural_height=5, input_empty=True)

    assert state.canvas_height_floor == 5
    assert state.input_growth_active is False
    assert state.input_canvas_saturated is True
    assert state.bottom_anchor.footprint_height == 0
    assert state.bottom_anchor.completion_visible is False
    assert state.bottom_release_height(occupied_height=4) == 20


def test_reexpanded_input_reuses_unconsumed_bottom_release() -> None:
    state = InlineLayoutState(canvas_height_floor=12)
    state.observe_input_layout(
        input_height=21,
        footprint_height=24,
        stable_line_baseline=4,
        live_height_baseline=2,
    )
    state.fit_canvas_height(natural_height=24, available_height=24)
    state.settle_input(natural_height=24, input_empty=True)

    state.observe_input_layout(
        input_height=2,
        footprint_height=5,
        stable_line_baseline=4,
        live_height_baseline=2,
    )

    assert state.input_growth_baseline == 24
    assert state.input_canvas_saturated is True
    assert state.input_anchor.footprint_height == 24
    assert state.bottom_release_height(occupied_height=5) == 19
