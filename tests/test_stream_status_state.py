# -*- coding: utf-8 -*-

import pytest

from mind_app.stream_state.status import StatusState
from mind_core.design import Design


STATUS_FAMILIES = (
    "tool",
    "mode",
    "wait",
)


def test_status_clear_is_immediate() -> None:
    state = StatusState()
    state.set_status("working", family="tool")

    assert state.visible is True
    assert state.clear_status() is True
    assert state.visible is False
    assert state.animating is False
    assert not state.renderable()


@pytest.mark.parametrize("family", STATUS_FAMILIES)
def test_status_families_use_one_refresh_rate(family: str) -> None:
    state = StatusState()
    state.set_status("working", family=family)

    assert Design.status_refresh_per_second(family) == 30
    assert state.refresh_per_second() == 30
    assert state.interval() == pytest.approx(1 / 30)


@pytest.mark.parametrize("family", STATUS_FAMILIES)
def test_status_families_start_with_single_dot(family: str) -> None:
    state = StatusState()
    state.set_status("working", family=family)

    plain = state.renderable().plain

    assert plain[0] in {"\u25e6", "\u2022"}
    assert not plain.startswith("[")
    assert not plain.startswith("(")


def test_status_dot_switches_between_hollow_and_solid() -> None:
    hollow = Design.tool_status_renderable(0.0, "working").plain[0]
    solid = Design.tool_status_renderable(4.0, "working").plain[0]

    assert hollow == "\u25e6"
    assert solid == "\u2022"
