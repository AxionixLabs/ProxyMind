# -*- coding: utf-8 -*-

import asyncio
import contextlib
import io
import json

import mind as mind_module
from mind_app.output.content import AssistantTextDelta
from mind_app.output.jsonl import create_json_output_session
from mind_app.output.text import create_text_output_session
from mind_app.presentation.models import (
    NativeToolResultView,
    RunCompletedView,
    RunStartedView,
)
from mind_app.stream_events.tool_traces.native import render_tool_result_entries


def run(coro):
    return asyncio.run(coro)


def started_view() -> RunStartedView:
    return RunStartedView(
        thread_id="thread-1",
        turn_id="turn-1",
        message="检查并修复测试",
        mode="fast",
        model="test-model",
        workdir="/project",
        sandbox="safe",
    )


def native_view() -> NativeToolResultView:
    data = {
        "command": "pytest -q",
        "stdout": "12 passed\n",
        "exit_code": 0,
    }
    return NativeToolResultView(
        name="exec_command",
        arguments={"command": "pytest -q", "cwd": "/project"},
        ok=True,
        data=data,
        cost_ms=1820,
        entries=tuple(render_tool_result_entries(
            "exec_command",
            {"command": "pytest -q"},
            ok=True,
            data=data,
            cost_ms=1820,
        )),
        call_id="item_0",
    )


def test_text_output_separates_process_and_assistant_streams(tmp_path) -> None:
    """文本输出会话把过程块和正文写入不同输出流。"""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        session = create_text_output_session(str(tmp_path / "text.log"))

        async def scenario() -> None:
            await session.control.open()
            await session.presentation.emit(started_view())
            session.control.record_tool_arguments(
                "exec_command",
                {"command": "pytest -q", "cwd": "/project"},
                call_id="item_0",
            )
            await session.presentation.emit(native_view())
            await session.content.emit(AssistantTextDelta("全部测试通过。"))
            await session.control.stop()

        run(scenario())

    assert "exec\npytest -q in /project" in stderr.getvalue()
    assert "succeeded in 1820ms:" in stderr.getvalue()
    assert "12 passed" in stderr.getvalue()
    assert "codex\n" in stderr.getvalue()
    assert "全部测试通过。" in stdout.getvalue()


def test_json_output_is_jsonl_and_flushes_each_event(tmp_path) -> None:
    """结构化输出会话按事件逐行写出完整对象。"""
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        session = create_json_output_session(str(tmp_path / "events.jsonl"))

        async def scenario() -> None:
            await session.control.open()
            await session.presentation.emit(started_view())
            session.control.record_tool_arguments(
                "exec_command",
                {"command": "pytest -q"},
                call_id="item_0",
            )
            await session.content.emit(AssistantTextDelta("全部测试通过。"))
            await session.control.settle_stream()
            await session.presentation.emit(native_view())
            await session.presentation.emit(RunCompletedView(usage={"output_tokens": 12}))
            await session.control.stop()

        run(scenario())

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [line["type"] for line in lines] == [
        "thread.started",
        "turn.started",
        "item.started",
        "item.completed",
        "item.completed",
        "turn.completed",
    ]
    assert lines[2]["item"]["type"] == "command_execution"
    assert lines[2]["item"]["status"] == "in_progress"
    assert lines[2]["item"]["id"] == "item_0"
    assert lines[3]["item"]["type"] == "agent_message"
    assert lines[3]["item"]["id"] == "item_1"
    assert lines[4]["item"]["aggregated_output"] == "12 passed\n"
    assert lines[4]["item"]["id"] == "item_0"
    assert lines[5]["usage"] == {"output_tokens": 12}


def test_json_output_normalizes_non_finite_numbers(tmp_path) -> None:
    """结构化输出不会写出 JSON 标准之外的数值字面量。"""
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        session = create_json_output_session(str(tmp_path / "events.jsonl"))

        async def scenario() -> None:
            await session.control.open()
            await session.presentation.emit(
                RunCompletedView(usage={"ratio": float("nan")})
            )
            await session.control.stop()

        run(scenario())

    assert json.loads(stdout.getvalue()) == {
        "type": "turn.completed",
        "usage": {"ratio": None},
    }


def test_cli_json_failure_is_one_complete_event() -> None:
    """入口级失败保持 stdout 为可逐行解析的 JSON。"""
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        mind_module.emit_json_failure("invalid arguments", phase="runtime")

    assert json.loads(stdout.getvalue()) == {
        "type": "turn.failed",
        "error": "invalid arguments",
        "phase": "runtime",
    }
    assert mind_module.json_output_requested(["--chat", "run", "--json"])
    assert not mind_module.json_output_requested(["--chat", "run"])


if __name__ == '__main__':
    pass
