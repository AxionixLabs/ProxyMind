# -*- coding: utf-8 -*-

import io

from frontends.tui.runtime import terminal_stderr


def test_stderr_targets_terminal_requires_both_interactive_streams(
    monkeypatch,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(terminal_stderr.sys, "stdout", stdout)
    monkeypatch.setattr(terminal_stderr.sys, "stderr", stderr)

    assert terminal_stderr._stderr_targets_terminal() is False


def test_stderr_guard_is_inert_when_output_is_redirected(monkeypatch) -> None:
    monkeypatch.setattr(
        terminal_stderr,
        "_stderr_targets_terminal",
        lambda: False,
    )

    guard = terminal_stderr.TerminalStderrGuard.install()
    try:
        assert guard._active is False
        assert guard._saved_fd is None
    finally:
        guard.close()
