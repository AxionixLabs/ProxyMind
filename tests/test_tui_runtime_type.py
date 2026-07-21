# -*- coding: utf-8 -*-

import pytest

from mind_app.frontend.contracts import PassiveFrontendRuntime
from mind_app.tui.core.runtime import (
    TuiRuntime,
    require_tui_runtime
)


def test_require_tui_runtime_returns_concrete_runtime() -> None:
    runtime = TuiRuntime()

    assert require_tui_runtime(runtime) is runtime


def test_require_tui_runtime_rejects_passive_runtime() -> None:
    with pytest.raises(TypeError, match="TUI frontend requires TuiRuntime"):
        require_tui_runtime(PassiveFrontendRuntime())
