# -*- coding: utf-8 -*-

from unittest.mock import AsyncMock

import pytest

from frontends.tui.adapters.clipboard import (
    OSC52_MAX_RAW_BYTES,
    ClipboardEnvironment,
    ClipboardError,
    _copy_text_to_clipboard_with,
    clipboard_environment,
    osc52_sequence,
)


def _environment(
    *,
    ssh: bool = False,
    tmux: bool = False,
    wsl: bool = False,
) -> ClipboardEnvironment:
    return ClipboardEnvironment(
        platform_name="linux",
        ssh_session=ssh,
        tmux_session=tmux,
        wsl_session=wsl,
    )


def test_clipboard_environment_detects_ssh_tmux_and_wsl_without_mutation() -> None:
    environment = clipboard_environment(
        {
            "SSH_CONNECTION": "client server",
            "TMUX_PANE": "%1",
            "WSL_DISTRO_NAME": "Ubuntu",
        },
        platform_name="linux",
        release="Linux",
    )

    assert environment == _environment(ssh=True, tmux=True, wsl=True)


@pytest.mark.anyio
async def test_ssh_copy_skips_remote_native_clipboard() -> None:
    native = AsyncMock()
    wsl = AsyncMock()
    terminal = AsyncMock()

    await _copy_text_to_clipboard_with(
        "answer",
        environment=_environment(ssh=True),
        native_copy=native,
        wsl_copy=wsl,
        terminal_copy=terminal,
    )

    terminal.assert_awaited_once_with("answer")
    native.assert_not_awaited()
    wsl.assert_not_awaited()


@pytest.mark.anyio
async def test_wsl_copy_uses_powershell_before_terminal_fallback() -> None:
    native = AsyncMock(side_effect=ClipboardError("native unavailable"))
    wsl = AsyncMock()
    terminal = AsyncMock()

    await _copy_text_to_clipboard_with(
        "answer",
        environment=_environment(wsl=True),
        native_copy=native,
        wsl_copy=wsl,
        terminal_copy=terminal,
    )

    native.assert_awaited_once_with("answer")
    wsl.assert_awaited_once_with("answer")
    terminal.assert_not_awaited()


@pytest.mark.anyio
async def test_local_copy_falls_back_to_terminal_after_native_failure() -> None:
    native = AsyncMock(side_effect=ClipboardError("native unavailable"))
    terminal = AsyncMock()

    await _copy_text_to_clipboard_with(
        "answer",
        environment=_environment(),
        native_copy=native,
        wsl_copy=AsyncMock(),
        terminal_copy=terminal,
    )

    terminal.assert_awaited_once_with("answer")


@pytest.mark.anyio
async def test_copy_reports_all_wsl_fallback_failures() -> None:
    with pytest.raises(ClipboardError) as raised:
        await _copy_text_to_clipboard_with(
            "answer",
            environment=_environment(tmux=True, wsl=True),
            native_copy=AsyncMock(
                side_effect=ClipboardError("native unavailable")
            ),
            wsl_copy=AsyncMock(
                side_effect=ClipboardError("powershell unavailable")
            ),
            terminal_copy=AsyncMock(
                side_effect=ClipboardError("terminal blocked")
            ),
        )

    assert str(raised.value) == (
        "native clipboard: native unavailable; "
        "WSL fallback: powershell unavailable; "
        "terminal fallback: terminal blocked"
    )


@pytest.mark.anyio
async def test_ssh_copy_reports_osc52_context() -> None:
    with pytest.raises(
        ClipboardError,
        match="OSC 52 clipboard copy failed over SSH: blocked",
    ):
        await _copy_text_to_clipboard_with(
            "answer",
            environment=_environment(ssh=True),
            native_copy=AsyncMock(),
            wsl_copy=AsyncMock(),
            terminal_copy=AsyncMock(side_effect=ClipboardError("blocked")),
        )


def test_osc52_sequence_matches_plain_and_tmux_contract() -> None:
    assert osc52_sequence("hi", tmux_session=False) == "\x1b]52;c;aGk=\x07"
    assert osc52_sequence("hi", tmux_session=True) == (
        "\x1bPtmux;\x1b\x1b]52;c;aGk=\x07\x1b\\"
    )


def test_osc52_sequence_rejects_oversized_utf8_payload() -> None:
    text = "界" * ((OSC52_MAX_RAW_BYTES // 3) + 1)

    with pytest.raises(ClipboardError, match="OSC 52 payload too large"):
        osc52_sequence(text, tmux_session=False)
