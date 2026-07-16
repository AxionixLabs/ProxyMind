# -*- coding: utf-8 -*-

import pytest

from mind_app.stream_state.status import StatusState
from mind_core.design import Design


STATUS_FAMILIES = (
    "builtin",
    "tool",
    "code",
    "mode",
    "wait",
    "heal",
    "loop",
)


def test_status_clear_is_immediate() -> None:
    state = StatusState()
    state.set_status("working", family="builtin")

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
