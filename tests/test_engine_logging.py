# -*- coding: utf-8 -*-

import io
from rich.console import Console
from engine import tinker


def test_active_logging_uses_explicit_console(monkeypatch) -> None:
    """日志激活器使用调用方传入的控制台。"""
    console = Console(
        file=io.StringIO(),
        force_terminal=False,
        color_system=None,
    )
    added: list[tuple[object, str, str]] = []

    monkeypatch.setattr(tinker.logger, "remove", lambda: None)
    monkeypatch.setattr(
        tinker.logger,
        "add",
        lambda sink, *, level, format: added.append((sink, level, format)),
    )

    tinker.Active.active("INFO", console=console)

    assert len(added) == 1
    sink, level, log_format = added[0]
    assert isinstance(sink, tinker.Active._RichSink)
    assert sink.console is console
    assert level == "INFO"
    assert log_format == tinker.const.PRINT_FORMAT


def test_active_logging_stderr_overrides_explicit_console(monkeypatch) -> None:
    """stderr 模式忽略普通控制台以保持 stdout 纯净。"""
    regular_console = Console(file=io.StringIO(), force_terminal=False)
    stderr_console = Console(file=io.StringIO(), force_terminal=False)
    created: list[dict] = []
    added: list[object] = []

    def create_console(**kwargs):
        created.append(kwargs)
        return stderr_console

    monkeypatch.setattr(tinker, "Console", create_console)
    monkeypatch.setattr(tinker.logger, "remove", lambda: None)
    monkeypatch.setattr(
        tinker.logger,
        "add",
        lambda sink, **kwargs: added.append(sink),
    )

    tinker.Active.active(
        "DEBUG",
        console=regular_console,
        stderr=True,
    )

    assert created == [{"stderr": True}]
    assert len(added) == 1
    assert added[0].console is stderr_console
