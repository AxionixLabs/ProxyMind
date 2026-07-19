# -*- coding: utf-8 -*-

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
import mind_app.stream_ui as stream_ui_module
from engine.tinker import MindError
from mind_app.mind_entry import (
    resolve_cli_frontend,
    resolve_cli_interaction,
    resolve_cli_output_mode
)
from mind_app.output.factory import create_output_session
from mind_app.output.factory import resolve_session_factory
from mind_app.output.contracts import OutputControlPort, OutputPort
from mind_app.output.jsonl import JsonOutputControl
from mind_app.output.jsonl import create_json_output_session
from mind_app.output.legacy_content import LegacyContentSink
from mind_app.output.text import TextOutputControl, create_text_output_session
from mind_app.presentation.legacy import LegacyPresentationSink
from mind_app.interaction.noninteractive import NonInteractiveInteraction
from mind_app.interaction.legacy import LegacyInteraction
from mind_app.frontend import (
    ConsoleApplicationSink,
    Frontend,
    SilentApplicationSink
)
from mind_app.mind_core import Mind
from mind_app.stream_ui import StreamUI

ROOT = Path(__file__).resolve().parents[1]


class FakeStreamUI(object):
    """记录默认输出会话传入的构造参数。"""

    def __init__(self, log_file: str, *, animate: bool) -> None:
        self.log_file = log_file
        self.animate = animate


def test_default_output_session_shares_one_control(monkeypatch) -> None:
    """默认正文和展示适配器共享同一个控制输出端。"""
    monkeypatch.setattr(stream_ui_module, "StreamUI", FakeStreamUI)

    output_session = create_output_session("run.log")

    assert isinstance(output_session.control, FakeStreamUI)
    assert output_session.control.log_file == "run.log"
    assert output_session.control.animate is True
    assert isinstance(output_session.content, LegacyContentSink)
    assert output_session.content.output is output_session.control
    assert isinstance(output_session.presentation, LegacyPresentationSink)
    assert output_session.presentation.output is output_session.control


def test_output_controls_use_nominal_abstract_boundaries() -> None:
    """三种输出控制器显式实现对应抽象边界。"""
    assert issubclass(StreamUI, OutputPort)
    assert issubclass(TextOutputControl, OutputControlPort)
    assert issubclass(JsonOutputControl, OutputControlPort)
    assert not inspect.isabstract(StreamUI)
    assert not inspect.isabstract(TextOutputControl)
    assert not inspect.isabstract(JsonOutputControl)


def test_stream_runtime_only_assembles_output_session() -> None:
    """流式事件循环不再装配具体终端输出适配器。"""
    source = (ROOT / "mind_app/modes/stream.py").read_text(encoding="utf-8")

    assert "LegacyContentSink" not in source
    assert "LegacyPresentationSink" not in source
    assert 'kwargs.pop("content_sink"' not in source
    assert 'kwargs.pop("presentation_sink"' not in source
    assert 'kwargs.pop("output_factory"' not in source


def test_runtime_control_does_not_depend_on_legacy_render_port() -> None:
    """运行控制模块不再依赖终端可见输出方法。"""
    paths = [
        "mind_app/runtime/support/loop_support.py",
        "mind_app/runtime/tools/batch.py",
        "mind_app/runtime/tools/batch_display.py",
        "mind_app/runtime/tools/enhance_reporter.py",
        "mind_app/runtime/tools/plan_call.py",
        "mind_app/runtime/tools/run.py",
        "mind_app/stream_events/lifecycle.py",
    ]

    for relative_path in paths:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "OutputPort" not in source
        assert ".feed(" not in source


def test_mind_owns_frontend_boundary() -> None:
    """Mind 只持有聚合后的前端边界。"""
    def factory(*_args, **_kwargs):
        return None

    frontend = Frontend(
        application=SilentApplicationSink(),
        interaction=LegacyInteraction(),
        session_factory=factory,
    )

    mind = Mind(
        [],
        "show",
        1,
        {},
        "gravity",
        None,
        src_opera_place="/tmp/mind",
        src_total_place="/tmp/reports",
        pref=SimpleNamespace(),
        frontend=frontend,
    )

    assert mind.frontend is frontend


def test_output_mode_resolves_without_changing_default_factory() -> None:
    """输出模式只选择会话工厂，不改变默认装配。"""
    assert resolve_session_factory("tui") is create_output_session
    assert resolve_session_factory("text") is create_text_output_session
    assert resolve_session_factory("json") is create_json_output_session


def test_cli_output_mode_follows_execution_entry() -> None:
    """命令入口在构造 Mind 前确定输出模式。"""
    base = {
        "chat": None,
        "fast": None,
        "xtra": None,
        "agent": False,
        "code": None,
        "json": False,
    }

    assert resolve_cli_output_mode(SimpleNamespace(**base)) == "tui"
    assert resolve_cli_output_mode(SimpleNamespace(**{**base, "chat": "hello"})) == "text"
    assert resolve_cli_output_mode(SimpleNamespace(**{**base, "agent": True})) == "text"
    assert resolve_cli_output_mode(SimpleNamespace(
        **{**base, "fast": "check", "json": True}
    )) == "json"

    text_interaction = resolve_cli_interaction(SimpleNamespace(
        **{**base, "chat": "hello"}
    ))
    json_interaction = resolve_cli_interaction(SimpleNamespace(
        **{**base, "fast": "check", "json": True}
    ))

    assert isinstance(text_interaction, NonInteractiveInteraction)
    assert isinstance(json_interaction, NonInteractiveInteraction)
    assert resolve_cli_interaction(SimpleNamespace(**base)) is None
    assert resolve_cli_interaction(SimpleNamespace(**{**base, "agent": True})) is None


def test_cli_frontend_aggregates_application_interaction_and_sessions() -> None:
    """CLI 在构造 Mind 前完成前端能力装配。"""
    base = {
        "chat": None,
        "fast": None,
        "xtra": None,
        "agent": False,
        "code": None,
        "json": False,
    }

    tui = resolve_cli_frontend(SimpleNamespace(**base), "tui")
    text = resolve_cli_frontend(
        SimpleNamespace(**{**base, "chat": "hello"}),
        "text",
    )
    json_frontend = resolve_cli_frontend(
        SimpleNamespace(**{**base, "fast": "check", "json": True}),
        "json",
    )

    assert isinstance(tui.application, ConsoleApplicationSink)
    assert isinstance(tui.interaction, LegacyInteraction)
    assert tui.session_factory is create_output_session
    assert isinstance(text.interaction, NonInteractiveInteraction)
    assert text.session_factory is create_text_output_session
    assert isinstance(json_frontend.application, SilentApplicationSink)
    assert isinstance(json_frontend.interaction, NonInteractiveInteraction)
    assert json_frontend.session_factory is create_json_output_session


@pytest.mark.parametrize(
    "overrides",
    [
        {"json": True},
        {"json": True, "agent": True},
        {"json": True, "chat": "run", "code": ["cases.md"]},
    ],
)
def test_json_output_rejects_non_stream_entries(overrides) -> None:
    """JSONL 当前只接受三个直接流式请求入口。"""
    values = {
        "chat": None,
        "fast": None,
        "xtra": None,
        "agent": False,
        "code": None,
        "json": False,
        **overrides,
    }

    with pytest.raises(MindError, match="--json requires --chat, --fast, or --xtra"):
        resolve_cli_output_mode(SimpleNamespace(**values))


if __name__ == '__main__':
    pass
