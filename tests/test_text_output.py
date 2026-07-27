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
from mind_app.output.content import AssistantTextDelta
from mind_app.presentation.run_views import build_run_started_view
from mind_app.runtime.support.calling import run_mode_lifecycle
from mind_app.stream_events.worked import worked_footer_text
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
        mode="chat",
        pref_config={
            "primary": {
                "model": "gpt-5.6-sol",
                "provider": "openai",
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
        mode="chat",
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
    assert "provider: OpenAI" in recorded
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
    control = TextOutputControl(state)

    await content.emit(AssistantTextDelta("first "))
    await content.emit(AssistantTextDelta("second"))

    assert stdout.getvalue() == ""
    await control.settle_stream()
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

    assert command_requests_outro(AgentListenCommand(), output_stream=terminal)
    assert command_requests_outro(HelixUpgradeCommand(), output_stream=terminal)
    assert not command_requests_outro(
        ExecCommand(prompt="hello"),
        output_stream=terminal,
    )


def test_elapsed_footer_uses_finished_label() -> None:
    footer = worked_footer_text(1.25, width=40)

    assert "Finished in 1.2s" in footer
    assert "Worked for" not in footer


def test_non_interactive_rich_mode_disables_entry_outro() -> None:
    output = SimpleNamespace(isatty=lambda: False)

    assert not command_requests_outro(
        AgentListenCommand(),
        output_stream=output,
    )


def test_entry_outro_uses_typed_command(monkeypatch) -> None:
    application = _Application()
    terminal = SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(entry, "_entry_application", lambda _command: application)

    emit_entry_outro(AgentListenCommand(), output_stream=terminal)

    assert [view.type for view in application.views] == ["outro"]


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

    await run_mode_lifecycle(mind, runner, mode="chat")

    assert application.views == []
