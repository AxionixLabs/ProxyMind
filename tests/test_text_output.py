# -*- coding: utf-8 -*-

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mind import entry_animation_requested, emit_entry_outro
from mind_app.output.text import (
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_MAGENTA,
    ANSI_RESET,
    TextOutputState,
    TextPresentationSink,
)
from mind_app.presentation.run_views import build_run_started_view
from mind_app.runtime.support.calling import run_mode_lifecycle


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
        access_mode="full",
        turn_id="turn-test",
    )


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
    assert visible.startswith("Mind v1.1.9\n")
    assert f"{ANSI_BOLD}workdir:{ANSI_RESET}" in visible
    assert f"{ANSI_CYAN}user\n{ANSI_RESET}" in visible
    assert f"{ANSI_MAGENTA}mind\n{ANSI_RESET}" in visible
    assert "provider: OpenAI" in recorded
    assert "approval: never" in recorded
    assert "sandbox: workspace-write [workdir, /tmp, $TMPDIR]" in recorded
    assert "reasoning effort: high" in recorded
    assert "reasoning summaries: none" in recorded
    assert "session id: sid-test" in recorded
    assert "codex" not in visible.lower()
    assert "\x1b[" not in recorded
    assert stdout.getvalue() == "Mind project.\n"


def test_direct_text_commands_disable_entry_animation() -> None:
    assert not entry_animation_requested(["--chat", "hello"])
    assert not entry_animation_requested(["--fast=hello"])
    assert not entry_animation_requested(["--xtra", "hello"])
    assert not entry_animation_requested(["--chat", "hello", "--code", "a.md"])
    assert entry_animation_requested([])
    assert entry_animation_requested(["--agent"])


def test_disabled_entry_animation_does_not_emit_outro() -> None:
    application = _Application()

    emit_entry_outro(application, enabled=False)

    assert application.views == []


@pytest.mark.anyio
async def test_non_animated_mode_does_not_emit_worked_footer() -> None:
    application = _Application()

    async def await_cleanup(awaitable) -> None:
        await awaitable

    mind = SimpleNamespace(
        animate=False,
        frontend=SimpleNamespace(application=application),
        start_anim=AsyncMock(),
        stop_anim=AsyncMock(),
        await_cleanup=await_cleanup,
    )
    runner = AsyncMock()

    await run_mode_lifecycle(mind, runner, mode="chat")

    assert application.views == []
