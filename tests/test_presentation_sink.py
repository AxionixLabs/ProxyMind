# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mind_app.presentation.contracts import PresentationView
from mind_app.presentation.models import (
    GenericToolResultView,
    NativeToolResultView,
    ToolStartView,
)
from mind_app.runtime.tools import batch as batch_module
from mind_app.runtime.tools.batch import (
    PendingToolCall,
    ToolBatchExecutor,
)
from mind_app.runtime.tools.display import (
    show_tool_result,
    show_tool_start,
)
from mind_app.runtime.tools.run import ToolRunResult

ROOT = Path(__file__).resolve().parents[1]


class RecordingPresentationSink(object):
    """记录运行时发送的结构化展示数据。"""

    def __init__(self) -> None:
        self.views: list[PresentationView] = []

    async def emit(self, view: PresentationView) -> None:
        self.views.append(view)


class FakeOutput(object):
    """记录工具执行器保留的审计和状态行为。"""

    def __init__(self) -> None:
        self.audits: list[tuple[str, dict, str | None]] = []
        self.end_count = 0

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict,
        *,
        call_id: str | None = None,
    ) -> None:
        self.audits.append((name, arguments, call_id))

    async def end_status(self, *, immediate: bool = False) -> None:
        _ = immediate
        self.end_count += 1

    async def feed(self, *_args, **_kwargs) -> None:
        return None


def test_tool_display_sends_views_without_rendering() -> None:
    """工具展示入口只发送 View，不接触具体终端 renderer。"""
    presentation = RecordingPresentationSink()
    result = ToolRunResult(
        result={},
        ok=True,
        fields={},
        text="done",
        data={},
        cost_ms=3,
    )

    async def run() -> None:
        await show_tool_start(presentation, "sample_tool", {"value": 1})
        await show_tool_result(
            presentation,
            "sample_tool",
            {"value": 1},
            result,
        )

    asyncio.run(run())

    assert isinstance(presentation.views[0], ToolStartView)
    assert isinstance(presentation.views[1], GenericToolResultView)


def test_native_executor_keeps_audit_and_status_outside_presentation(
    monkeypatch,
) -> None:
    """native 工具的审计和状态收束仍由执行器负责。"""
    output = FakeOutput()
    presentation = RecordingPresentationSink()

    async def fake_run_tool_step(*_args, **_kwargs) -> ToolRunResult:
        return ToolRunResult(
            result={},
            ok=True,
            fields={"ok": True},
            text="hello",
            data={
                "command": "printf hello",
                "output_lines": ["hello"],
                "exit_code": 0,
            },
            cost_ms=4,
        )

    async def fake_post_tool_result(*_args, **_kwargs) -> dict:
        return {}

    monkeypatch.setattr(batch_module, "run_tool_step", fake_run_tool_step)
    monkeypatch.setattr(
        ToolBatchExecutor,
        "post_tool_result",
        staticmethod(fake_post_tool_result),
    )

    executor = ToolBatchExecutor(
        session=SimpleNamespace(),
        stream_ui=output,
        presentation=presentation,
        tools=[],
        mode="fast",
        pref_config={},
        metadata={},
        report=SimpleNamespace(),
    )
    pending = PendingToolCall(
        event={"cid": "cid", "sid": "sid", "call_id": "call-1"},
        name="shell_command",
        arguments={"command": "printf hello"},
        meta=None,
        execution=None,
        use_coding_trace=True,
    )

    asyncio.run(executor.execute_call(pending))

    assert output.audits == [
        ("shell_command", {"command": "printf hello"}, "call-1")
    ]
    assert output.end_count == 1
    assert len(presentation.views) == 1
    assert isinstance(presentation.views[0], NativeToolResultView)


def test_tool_runtime_does_not_import_rich_presentation() -> None:
    """工具运行时不再选择 Rich renderer 或 Design console。"""
    sources = [
        (ROOT / "mind_app/runtime/tools/display.py").read_text(encoding="utf-8"),
        (ROOT / "mind_app/runtime/tools/batch_display.py").read_text(encoding="utf-8"),
        (ROOT / "mind_app/runtime/tools/plan_call.py").read_text(encoding="utf-8"),
    ]

    for source in sources:
        assert "presentation.rich" not in source
        assert "mind_core.design" not in source
