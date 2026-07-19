# -*- coding: utf-8 -*-

from pathlib import Path

import mind_app.stream_ui as stream_ui_module
from mind_app.output.factory import create_output_session
from mind_app.output.legacy_content import LegacyContentSink
from mind_app.presentation.legacy import LegacyPresentationSink

ROOT = Path(__file__).resolve().parents[1]


class FakeStreamUI(object):
    """记录默认输出会话传入的构造参数。"""

    def __init__(self, log_file: str, *, design_level: str) -> None:
        self.log_file = log_file
        self.design_level = design_level


def test_default_output_session_shares_one_control(monkeypatch) -> None:
    """默认正文和展示适配器共享同一个控制输出端。"""
    monkeypatch.setattr(stream_ui_module, "StreamUI", FakeStreamUI)

    output_session = create_output_session(
        "run.log",
        design_level="show",
    )

    assert isinstance(output_session.control, FakeStreamUI)
    assert output_session.control.log_file == "run.log"
    assert output_session.control.design_level == "show"
    assert isinstance(output_session.content, LegacyContentSink)
    assert output_session.content.output is output_session.control
    assert isinstance(output_session.presentation, LegacyPresentationSink)
    assert output_session.presentation.output is output_session.control


def test_stream_runtime_only_assembles_output_session() -> None:
    """流式事件循环不再装配具体 Legacy 输出适配器。"""
    source = (ROOT / "mind_app/modes/stream.py").read_text(encoding="utf-8")

    assert "LegacyContentSink" not in source
    assert "LegacyPresentationSink" not in source
    assert 'kwargs.pop("content_sink"' not in source
    assert 'kwargs.pop("presentation_sink"' not in source
    assert 'kwargs.pop("output_factory"' not in source


if __name__ == '__main__':
    pass
