# -*- coding: utf-8 -*-

import asyncio
import io
from contextlib import contextmanager
from unittest.mock import (
    Mock,
    patch,
)

import pytest
from prompt_toolkit.input import create_pipe_input

from agent.domain.mcp_oauth import McpOAuthError
from frontends.terminal.oauth_input import HiddenOAuthCallbackInput


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["success", "paste", "cancel", "eof", "empty", "oversize", "timeout", "edit"])
async def test_hidden_input_bounds_and_terminal_restoration(outcome):
    output = io.StringIO()
    stdin = Mock(spec=io.TextIOBase)
    stdin.isatty.return_value = True
    events = []

    @contextmanager
    def raw_mode():
        events.append("raw")
        try:
            yield
        finally:
            events.append("restored")

    with create_pipe_input() as pipe, patch("frontends.terminal.oauth_input.hidden_terminal_mode", side_effect=lambda stream: raw_mode()), patch.object(pipe, "close", wraps=pipe.close) as close:
        reader = HiddenOAuthCallbackInput(stdin, output, input_factory=lambda: pipe)
        task = asyncio.create_task(reader.read_callback(max_bytes=80))
        try:
            await asyncio.sleep(0)
            secret = "http://127.0.0.1/callback?code=private-code"
            if outcome == "timeout":
                task.cancel()
            elif outcome == "paste":
                pipe.send_text(f"\x1b[200~{secret}\x1b[201~\r")
            elif outcome == "edit":
                pipe.send_text(f"discard\x15{secret}x\x7f\r")
            elif outcome == "oversize":
                pipe.send_text("私" * 30)
            else:
                pipe.send_text({"success": secret + "\r", "cancel": secret + "\x03", "eof": "\x04", "empty": "\r"}[outcome])
            if outcome in ("cancel", "timeout"):
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            elif outcome in ("eof", "empty", "oversize"):
                with pytest.raises(McpOAuthError) as error:
                    await asyncio.wait_for(task, 2)
                assert error.value.code == ("callback_input_too_long" if outcome == "oversize" else "callback_input_closed")
            else:
                assert await asyncio.wait_for(task, 2) == secret
            assert events == ["raw", "restored"]
            close.assert_called_once()
            assert "private-code" not in output.getvalue() and secret not in output.getvalue()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_manual_input_rejects_non_terminal_without_echo_fallback():
    with pytest.raises(McpOAuthError) as error:
        HiddenOAuthCallbackInput(io.StringIO(), io.StringIO())
    assert error.value.code == "callback_input_unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["enter", "exit", "close"])
async def test_terminal_mode_and_close_errors_are_fixed_failures(stage):
    stdin = Mock(spec=io.TextIOBase)
    stdin.isatty.return_value = True
    output = io.StringIO()

    @contextmanager
    def mode():
        if stage == "enter":
            raise OSError("private-platform-detail")
        try:
            yield
        finally:
            if stage == "exit":
                raise OSError("private-platform-detail")

    with create_pipe_input() as pipe:
        original_close = pipe.close

        def close_input():
            original_close()
            if stage == "close":
                raise OSError("private-platform-detail")

        with patch("frontends.terminal.oauth_input.hidden_terminal_mode", side_effect=lambda stream: mode()), patch.object(pipe, "close", side_effect=close_input):
            reader = HiddenOAuthCallbackInput(stdin, output, input_factory=lambda: pipe)
            pipe.send_text("private-callback\r")
            with pytest.raises(McpOAuthError) as error:
                await asyncio.wait_for(reader.read_callback(max_bytes=80), 2)
            assert error.value.code == "callback_input_unavailable"
            assert "private-" not in str(error.value) + output.getvalue()
