# -*- coding: utf-8 -*-

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mind_app.cli import entry
from mind_app.cli.commands import (
    AgentListenCommand,
    ExecCommand,
    HelixUpgradeCommand,
)
from mind_app.cli.entry import (
    command_requests_outro,
    emit_entry_outro,
)
from mind_app.frontend.contracts import PassiveFrontendRuntime
from mind_app.output.text import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_MAGENTA,
    ANSI_RESET,
    TextContentSink,
    TextOutputControl,
    TextOutputState,
    TextPresentationSink,
)
from mind_app.output.content import (
    AssistantSegmentCompleted,
    AssistantTextDelta,
)
from mind_app.presentation.models import (
    ApprovalView,
    GenericToolResultView,
    HookOutputView,
    HookRunView,
    ProgressView,
    TracePreview,
)
from mind_app.presentation.run_views import build_run_started_view
from mind_app.runtime.support.calling import run_turn_lifecycle
from mind_app.stream_events.worked import (
    emit_worked_footer,
    worked_footer_text,
)
from mind_core.permissions import preset_permissions


class _RecordWriter(object):
    def __init__(self) -> None:
        self.parts: list[str] = []

    @property
    def trailing_newlines(self) -> int:
        text = "".join(self.parts)
        return len(text) - len(text.rstrip("\n"))

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def write(self, text: str, *, block: bool = False) -> None:
        _ = block
        self.parts.append(text)

    def write_audit(self, text: str) -> None:
        _ = text

    def flush(self) -> None:
        return None


class _Application(object):
    def __init__(self) -> None:
        self.views = []
        self.viewport = SimpleNamespace(width=80, height=24)

    def emit(self, view) -> None:
        self.views.append(view)


def _run_view():
    return build_run_started_view(
        metadata={"cid": "cid-test", "sid": "sid-test"},
        message="What project is this?",
        pref_config={
            "primary": {
                "model": "gpt-5.6-sol",
                "provider": "openai-main",
                "name": "OpenAI Main",
                "reasoning_effort": "high",
            },
        },
        workdir=r"D:\PycharmProjects\Craft",
        permissions=preset_permissions("full-access"),
        turn_id="turn-test",
    )


def test_run_view_uppercases_windows_drive_letter_for_display() -> None:
    view = build_run_started_view(
        metadata={},
        message="inspect",
        pref_config={},
        workdir="d:/PycharmProjects/ProxyMind",
        permissions=preset_permissions("read-only"),
        turn_id="",
    )

    assert view.workdir == "D:/PycharmProjects/ProxyMind"


@pytest.mark.anyio
async def test_text_output_uses_static_mind_header_and_role_colors() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    record = _RecordWriter()
    state = TextOutputState(
        record_writer=record,
        stdout=stdout,
        stderr=stderr,
        color=True,
    )

    await TextPresentationSink(state).emit(_run_view())
    state.assistant("Mind project.\n")

    visible = stderr.getvalue()
    recorded = "".join(record.parts)
    assert f"{ANSI_BOLD}workdir:{ANSI_RESET}" in visible
    assert f"{ANSI_CYAN}user{ANSI_RESET}\n" in visible
    assert f"{ANSI_MAGENTA}mind{ANSI_RESET}\n" in visible
    assert "provider: OpenAI Main" in recorded
    assert "approval: never" in recorded
    assert "sandbox: danger-full-access" in recorded
    assert "reasoning effort: high" in recorded
    assert "reasoning summaries: none" in recorded
    assert "session id: sid-test" in recorded
    assert "codex" not in visible.lower()
    assert "\x1b[" not in recorded
    assert stdout.getvalue() == "Mind project.\n"


@pytest.mark.anyio
async def test_text_output_strips_external_ansi_and_resets_on_close() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    record = _RecordWriter()
    state = TextOutputState(
        record_writer=record,
        stdout=stdout,
        stderr=stderr,
        color=True,
    )

    state.process("\x1b[31mtool output\x1b[0m\n")
    state.assistant("\x1b[35massistant output\x1b[0m\n")
    await state.close()

    assert "tool output\n" in stderr.getvalue()
    assert stdout.getvalue() == "assistant output\n"
    assert stderr.getvalue().endswith(ANSI_RESET)
    assert "\x1b[31m" not in stderr.getvalue()
    assert "\x1b[35m" not in stdout.getvalue()
    assert "\x1b[" not in "".join(record.parts)


def test_text_tool_labels_are_colored_without_mcp_prefix_or_color_leak() -> None:
    stderr = io.StringIO()
    record = _RecordWriter()
    state = TextOutputState(
        record_writer=record,
        stdout=io.StringIO(),
        stderr=stderr,
        color=True,
    )
    control = TextOutputControl(state)

    control.record_tool_arguments("mcp__docs__search", {})
    control.record_tool_arguments(
        "exec_command",
        {"command": "pytest -q", "cwd": "D:/workspace"},
    )

    assert stderr.getvalue() == (
        f"{ANSI_CYAN}mcp__docs__search{ANSI_RESET}\n"
        f"{ANSI_CYAN}exec{ANSI_RESET}\n"
        "pytest -q in D:/workspace\n"
    )
    assert "mcp:" not in stderr.getvalue()
    assert "".join(record.parts) == (
        "mcp__docs__search\n"
        "exec\n"
        "pytest -q in D:/workspace\n"
    )


def test_text_js_repl_start_prints_source() -> None:
    stderr = io.StringIO()
    record = _RecordWriter()
    state = TextOutputState(
        record_writer=record,
        stdout=io.StringIO(),
        stderr=stderr,
        color=False,
    )
    control = TextOutputControl(state)
    source = "await host.tool('shell_command', {command: 'echo ready'});"

    control.record_tool_arguments(
        "js_repl",
        {"code": source, "timeout_ms": 30000},
    )

    assert stderr.getvalue() == f"JavaScript\n{source}\n"
    assert "".join(record.parts) == f"JavaScript\n{source}\n"


@pytest.mark.anyio
async def test_text_tool_events_color_only_the_tool_name() -> None:
    stderr = io.StringIO()
    state = TextOutputState(
        record_writer=_RecordWriter(),
        stdout=io.StringIO(),
        stderr=stderr,
        color=True,
    )
    sink = TextPresentationSink(state)

    await sink.emit(GenericToolResultView(
        name="mcp__docs__search",
        text="result",
        ok=True,
        title="",
        preview=TracePreview(),
    ))
    await sink.emit(ProgressView(
        text="loading",
        source="tool",
        tool_name="mcp__docs__search",
    ))

    assert stderr.getvalue() == (
        f"{ANSI_CYAN}mcp__docs__search{ANSI_RESET} succeeded:\n"
        "result\n"
        f"{ANSI_CYAN}mcp__docs__search{ANSI_RESET}\n"
        "loading\n"
    )
    assert "mcp:" not in stderr.getvalue()


@pytest.mark.anyio
async def test_text_hook_and_policy_approval_are_progress_on_stderr() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    state = TextOutputState(
        record_writer=_RecordWriter(),
        stdout=stdout,
        stderr=stderr,
    )
    sink = TextPresentationSink(state)

    await sink.emit(HookRunView(
        id="hook-1",
        hook_key="project:prompt",
        event="UserPromptSubmit",
        phase="started",
        status="running",
        status_message="Checking prompt",
    ))
    await sink.emit(HookRunView(
        id="hook-1",
        hook_key="project:prompt",
        event="UserPromptSubmit",
        phase="completed",
        status="completed",
        status_message="Checking prompt",
        duration_ms=25,
        entries=(HookOutputView("context", "safe context"),),
    ))
    await sink.emit(ApprovalView(
        approval={"tool": "shell_command", "command": "pytest -q"},
        decision="decline",
        state="denied",
        source="policy",
    ))

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "• Running UserPromptSubmit hook: Checking prompt\n"
        "• UserPromptSubmit hook: Checking prompt\n"
        "└ completed · 25ms\n"
        "  hook context: safe context\n"
        "• Approval policy denied pytest -q\n"
    )


@pytest.mark.anyio
async def test_text_output_emits_assistant_text_after_stream_settles() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    state = TextOutputState(
        record_writer=_RecordWriter(),
        stdout=stdout,
        stderr=stderr,
    )
    content = TextContentSink(state)

    await content.emit(AssistantTextDelta("first "))
    await content.emit(AssistantTextDelta("second"))

    assert stdout.getvalue() == ""
    await content.emit(AssistantSegmentCompleted())
    assert stdout.getvalue() == "first second\n"


@pytest.mark.anyio
async def test_text_hidden_output_is_safe_for_display_logs() -> None:
    record = _RecordWriter()
    state = TextOutputState(
        record_writer=record,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    control = TextOutputControl(state)

    await control.record_hidden_output(
        "47031FDAQ001MK\tdevice\x1b]52;c;payload\x1b\\"
    )

    assert "".join(record.parts) == "47031FDAQ001MK  device"


def test_only_interactive_rich_commands_request_entry_outro() -> None:
    terminal = SimpleNamespace(isatty=lambda: True)

    assert not command_requests_outro(
        AgentListenCommand(),
        output_stream=terminal,
    )
    assert command_requests_outro(HelixUpgradeCommand(), output_stream=terminal)
    assert not command_requests_outro(
        ExecCommand(prompt="hello"),
        output_stream=terminal,
    )


def test_elapsed_footer_uses_finished_label() -> None:
    footer = worked_footer_text(1.25, width=40)

    assert "Finished in 1.2s" in footer
    assert "Worked for" not in footer


def test_elapsed_footer_emits_responsive_line_metadata() -> None:
    application = _Application()

    emit_worked_footer(application, 1.25)

    worked = application.views[0]
    assert worked.type == "run.worked"
    assert worked.payload == {"line_fill_character": "─"}


def test_non_interactive_rich_mode_disables_entry_outro() -> None:
    output = SimpleNamespace(isatty=lambda: False)

    assert not command_requests_outro(
        AgentListenCommand(),
        output_stream=output,
    )


def test_tui_agent_listen_does_not_emit_rich_outro(monkeypatch) -> None:
    application = _Application()
    terminal = SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(entry, "_entry_application", lambda _command: application)

    emit_entry_outro(AgentListenCommand(), output_stream=terminal)

    assert application.views == []


@pytest.mark.anyio
async def test_non_animated_mode_does_not_emit_worked_footer() -> None:
    application = _Application()

    async def await_cleanup(awaitable) -> None:
        await awaitable

    mind = SimpleNamespace(
        animate=False,
        frontend=SimpleNamespace(
            application=application,
            runtime=PassiveFrontendRuntime(),
        ),
        start_anim=AsyncMock(),
        stop_anim=AsyncMock(),
        await_cleanup=await_cleanup,
    )
    runner = AsyncMock()

    await run_turn_lifecycle(mind, runner)

    assert application.views == []


@pytest.mark.anyio
async def test_worked_footer_precedes_final_animation_cleanup() -> None:
    events: list[str] = []

    class Application(_Application):
        def emit(self, view) -> None:
            events.append(view.type)
            super().emit(view)

    async def stop_anim(_kind: str) -> None:
        events.append("anim.clear")

    async def await_cleanup(awaitable) -> None:
        await awaitable

    runtime = SimpleNamespace(
        begin_terminal_progress=lambda: events.append("progress.begin"),
        end_terminal_progress=lambda: events.append("progress.clear"),
    )
    mind = SimpleNamespace(
        animate=True,
        frontend=SimpleNamespace(
            application=Application(),
            runtime=runtime,
        ),
        start_anim=AsyncMock(side_effect=lambda: events.append("anim.begin")),
        stop_anim=stop_anim,
        await_cleanup=await_cleanup,
    )

    async def runner() -> None:
        events.append("runner")

    await run_turn_lifecycle(mind, runner)

    assert events == [
        "progress.begin",
        "anim.begin",
        "runner",
        "run.worked",
        "run.gap",
        "anim.clear",
        "progress.clear",
    ]
