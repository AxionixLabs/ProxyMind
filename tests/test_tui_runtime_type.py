# -*- coding: utf-8 -*-

from io import StringIO

import pytest
from prompt_toolkit.output.plain_text import PlainTextOutput

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


def test_exit_summary_renders_as_plain_terminal_text() -> None:
    stdout = StringIO()
    runtime = TuiRuntime(output_obj=PlainTextOutput(stdout))

    runtime.print_exit_summary()

    assert stdout.getvalue() == "\r\n■ Mind · session ended\r\n"
