# -*- coding: utf-8 -*-

import io
import json
from pathlib import Path
from rich.console import Console
from mind_core.design import Design
from mind_app.frontend import (
    ApplicationView,
    ConsoleApplicationSink,
    JsonApplicationSink,
    SilentApplicationSink
)

ROOT = Path(__file__).resolve().parents[1]


def test_console_application_sink_owns_entry_effects(monkeypatch) -> None:
    """终端应用输出端统一承接入口与退出效果。"""
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    calls: list[tuple[str, Console]] = []

    monkeypatch.setattr(
        Design,
        "show_intro",
        lambda target: calls.append(("intro", target)),
    )
    monkeypatch.setattr(
        Design,
        "show_outro",
        lambda target: calls.append(("outro", target)),
    )

    sink = ConsoleApplicationSink(console)
    sink.emit(ApplicationView(type="intro"))
    sink.emit(ApplicationView(type="notice", renderable="ready"))
    sink.emit(ApplicationView(type="outro"))

    assert calls == [("intro", console), ("outro", console)]
    assert stream.getvalue() == "ready\n"


def test_json_application_sink_only_writes_json_events() -> None:
    """JSON 应用输出端忽略所有面向人的入口展示。"""
    stream = io.StringIO()
    sink = JsonApplicationSink(stream)

    sink.emit(ApplicationView(type="intro"))
    sink.emit(ApplicationView(type="notice", renderable="human text"))
    sink.emit(ApplicationView(
        type="json",
        renderable={"type": "turn.failed", "error": "failed"},
    ))
    sink.emit(ApplicationView(type="outro"))

    assert json.loads(stream.getvalue()) == {
        "type": "turn.failed",
        "error": "failed",
    }


def test_silent_application_sink_has_no_viewport_or_output() -> None:
    """静默应用输出端不产生展示副作用。"""
    sink = SilentApplicationSink()

    sink.emit(ApplicationView(type="notice", renderable="ignored"))

    assert sink.viewport.width is None
    assert sink.viewport.height is None


def test_console_application_sink_renders_runtime_update() -> None:
    """终端应用输出端负责渲染运行时更新提示。"""
    stream = io.StringIO()
    sink = ConsoleApplicationSink(Console(
        file=stream,
        force_terminal=False,
        color_system=None,
    ))

    sink.emit(ApplicationView(
        type="runtime.update_available",
        payload={
            "local": {"version": "1.0.0"},
            "remote": {"version": "1.1.0", "notes": "fixes"},
        },
    ))

    output = stream.getvalue()
    assert "update available" in output
    assert "1.0.0" in output
    assert "1.1.0" in output
    assert "fixes" in output


def test_entry_lifecycle_has_no_direct_console_writes() -> None:
    """应用入口只负责发送 Frontend 展示数据。"""
    source = (ROOT / "mind_app/mind_entry.py").read_text(encoding="utf-8")
    launcher = (ROOT / "mind.py").read_text(encoding="utf-8")

    assert "Design.console.print" not in source
    assert "Design.console" not in source
    assert "Design.show_intro" not in source
    assert "Design.show_outro" not in launcher
    assert "Design.Doc" not in launcher


def test_repl_core_has_no_global_console_dependency() -> None:
    """REPL 主循环和每轮核心展示只通过 Frontend 输出。"""
    paths = [
        "engine/tinker.py",
        "engine/manage.py",
        "mind_app/modes/repl.py",
        "mind_app/modes/agent/ui.py",
        "mind_app/modes/batch.py",
        "mind_app/modes/support/repl_commands.py",
        "mind_app/modes/support/repl_model.py",
        "mind_app/modes/support/repl_diff.py",
        "mind_app/modes/support/repl_mcp.py",
        "mind_app/modes/support/repl_permissions.py",
        "mind_app/modes/support/repl_ps.py",
        "mind_app/modes/support/repl_shell.py",
        "mind_app/modes/support/repl_summary.py",
        "mind_app/modes/support/repl_tools.py",
        "mind_app/modes/support/repl_turn.py",
        "mind_app/stream_events/worked.py",
        "mind_core/authorize.py",
    ]

    for relative_path in paths:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "Design.console" not in source
        assert "Design.Doc" not in source
        assert "DESIGN_CONSOLE" not in source


def test_design_and_build_have_no_global_console_dependency() -> None:
    """Design、升级流程和构建入口只使用显式实例控制台。"""
    paths = [
        "build.py",
        "mind_app/assets.py",
        "mind_core/design/facade.py",
        "mind_core/design/utils.py",
    ]

    for relative_path in paths:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "Design.console" not in source
        assert "DESIGN_CONSOLE" not in source


if __name__ == '__main__':
    pass
