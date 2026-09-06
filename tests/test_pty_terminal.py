import os
import sys
from pathlib import Path

import pytest

from tests.support.pty import PtyKey
from tests.support.pty import TerminalEnvironment
from tests.support.pty import TerminalInputSource
from tests.support.pty import TerminalMode
from tests.support.pty import TerminalQuery
from tests.support.pty import TerminalQueryResponder
from tests.support.pty import TerminalReplyConfig
from tests.support.pty import TerminalScreen
from tests.support.pty import TerminalSize
from tests.support.pty import spawn_terminal


pytestmark = pytest.mark.pty_acceptance


def test_terminal_environment_replaces_host_identity() -> None:
    """验证验收子进程不会继承宿主终端身份或颜色覆盖。"""
    base = {
        "PATH": os.environ.get("PATH", ""),
        "TERM": "dumb",
        "NO_COLOR": "1",
        "FORCE_COLOR": "0",
        "WT_SESSION": "host-session",
    }

    environment = TerminalEnvironment().derive(base)

    assert environment["PATH"] == base["PATH"]
    assert environment["TERM"] == "xterm-256color"
    assert environment["COLORTERM"] == "truecolor"
    assert environment["TERM_PROGRAM"] == "WezTerm"
    assert environment["TERM_PROGRAM_VERSION"] == "2026.1"
    assert "NO_COLOR" not in environment
    assert "FORCE_COLOR" not in environment
    assert "WT_SESSION" not in environment


def test_query_responder_bounds_partial_and_unknown_sequences() -> None:
    """验证截断、超长和未知序列不会形成无界缓存。"""
    config = TerminalReplyConfig(max_query_bytes=64)
    responder = TerminalQueryResponder(config)

    first = responder.feed(b"noise" * 4096 + b"\x1b]10;?")
    second = responder.feed(b"\x1b\\")
    third = responder.feed(b"\x1b[>7u\x1b[<u" + b"?" * 4096)

    assert not first.queries
    assert len(second.queries) == 1
    assert second.queries[0].query is TerminalQuery.DEFAULT_FOREGROUND
    assert second.queries[0].response == b"\x1b]10;rgb:eeee/eeee/eeee\x1b\\"
    assert [event.mode for event in third.modes] == [
        TerminalMode.KEYBOARD_ENHANCEMENT_ENABLED,
        TerminalMode.KEYBOARD_ENHANCEMENT_RESTORED,
    ]
    assert responder.buffered_bytes <= config.max_query_bytes


def test_query_responder_answers_all_supported_queries_once() -> None:
    """验证全部终端查询具有确定且单次的响应。"""
    responder = TerminalQueryResponder(
        TerminalReplyConfig(cursor_row=7, cursor_column=13)
    )
    batch = responder.feed(
        b"\x1b[6n"
        b"\x1b]10;?\x1b\\"
        b"\x1b]11;?\x07"
        b"\x1b[?u"
        b"\x1b[c"
    )

    assert [(event.query, event.response) for event in batch.queries] == [
        (TerminalQuery.CURSOR_POSITION, b"\x1b[7;13R"),
        (
            TerminalQuery.DEFAULT_FOREGROUND,
            b"\x1b]10;rgb:eeee/eeee/eeee\x1b\\",
        ),
        (
            TerminalQuery.DEFAULT_BACKGROUND,
            b"\x1b]11;rgb:1111/1111/1111\x1b\\",
        ),
        (TerminalQuery.KEYBOARD_ENHANCEMENT, b"\x1b[?7u"),
        (TerminalQuery.PRIMARY_DEVICE_ATTRIBUTES, b"\x1b[?64;1;2c"),
    ]


def test_terminal_screen_exposes_style_cursor_and_scrollback() -> None:
    """验证 Screen oracle 保留样式、光标、宽字符和历史行。"""
    styled = TerminalScreen(TerminalSize(rows=3, columns=10))
    styled.feed(b"\x1b[2J\x1b[H\x1b[1;38;5;196;48;2;1;2;3mX")
    styled.feed("世".encode("utf-8"))

    cell = styled.cell(0, 0)
    wide_cell = styled.cell(0, 1)
    snapshot = styled.snapshot()

    assert cell.data == "X"
    assert cell.foreground == "ff0000"
    assert cell.background == "010203"
    assert cell.bold
    assert wide_cell.data == "世"
    assert snapshot.cursor.row == 0
    assert snapshot.cursor.column == 3

    styled.feed(
        b"\r\n\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\"
        b"\x1b[?2026hSYNC\x1b[?2026l"
    )
    rich_snapshot = styled.wait_for_text("linkSYNC")
    assert "linkSYNC" in rich_snapshot.visible_text

    history = TerminalScreen(TerminalSize(rows=3, columns=10))
    history.feed(b"A\r\nB\r\nC\r\nD")
    history_snapshot = history.wait_for_text("D")

    assert history_snapshot.scrollback_lines == ("A",)
    assert history_snapshot.visible_lines[0].rstrip() == "B"
    assert history_snapshot.visible_lines[2].rstrip() == "D"
    assert history_snapshot.cursor == history.snapshot().cursor


def test_real_pty_replies_and_user_input_are_delivered_once(
    tmp_path: Path,
) -> None:
    """验证真实 PTY 查询响应与用户输入交错后完整且不重复。"""
    if os.name == "nt":
        queries = b"\x1b[?u\x1b[>7u\x1b[?1004h"
        expected_queries = frozenset({TerminalQuery.KEYBOARD_ENHANCEMENT})
        expected_responses = b"\x1b[?7u"
    else:
        queries = (
            b"\x1b[6n"
            b"\x1b]10;?\x1b\\"
            b"\x1b]11;?\x1b\\"
            b"\x1b[?u"
            b"\x1b[c"
            b"\x1b[>7u"
            b"\x1b[?1004h"
        )
        expected_queries = frozenset(TerminalQuery)
        expected_responses = (
            b"\x1b[7;13R"
            b"\x1b]10;rgb:eeee/eeee/eeee\x1b\\"
            b"\x1b]11;rgb:1111/1111/1111\x1b\\"
            b"\x1b[?7u"
            b"\x1b[?64;1;2c"
        )
    closing_modes = b"\x1b[<u\x1b[?1004l"
    source = (
        "import os,sys; "
        "print('ENV='+os.environ['TERM']+'|'+os.environ['COLORTERM']+'|'"
        "+os.environ['TERM_PROGRAM'],flush=True); "
        f"sys.stdout.buffer.write({queries!r}); sys.stdout.buffer.flush(); "
        "value=sys.stdin.buffer.readline().rstrip(b'\\r\\n'); "
        "print('INPUT-HEX='+value.hex(),flush=True); "
        f"sys.stdout.buffer.write({closing_modes!r}); sys.stdout.buffer.flush()"
    )
    replies = TerminalReplyConfig(cursor_row=7, cursor_column=13)
    user_input = "typed-🙂".encode("utf-8")
    expected_input = user_input if os.name == "nt" else expected_responses + user_input

    with spawn_terminal(
        [sys.executable, "-u", "-c", source],
        cwd=Path.cwd(),
        env=os.environ,
        size=TerminalSize(rows=12, columns=100),
        replies=replies,
    ) as terminal:
        query_events = terminal.wait_for_queries(expected_queries, timeout=1.0)
        terminal.wait_for_modes(
            {
                TerminalMode.KEYBOARD_ENHANCEMENT_ENABLED,
                TerminalMode.FOCUS_REPORTING_ENABLED,
            },
            timeout=1.0,
        )
        terminal.resize(TerminalSize(rows=14, columns=96))
        terminal.write_user_text("typed-🙂")
        terminal.send_key(PtyKey.ENTER)
        terminal.wait_for_screen_text(f"INPUT-HEX={expected_input.hex()}")
        assert terminal.wait_for_exit() == 0
        terminal.wait_for_modes(
            {
                TerminalMode.KEYBOARD_ENHANCEMENT_RESTORED,
                TerminalMode.FOCUS_REPORTING_DISABLED,
            },
        )

        assert "ENV=xterm-256color|truecolor|WezTerm" in terminal.session.output_text()
        assert {event.query for event in query_events} == expected_queries
        assert len(query_events) == len(expected_queries)
        response_events = [
            event
            for event in terminal.input_events
            if event.source is TerminalInputSource.TERMINAL_RESPONSE
        ]
        user_events = [
            event
            for event in terminal.input_events
            if event.source is TerminalInputSource.USER
        ]
        assert b"".join(event.data for event in response_events) == expected_responses
        assert [event.data for event in user_events] == [
            user_input,
            PtyKey.ENTER.value,
        ]
        terminal.save_failure_artifacts(tmp_path)
        assert (tmp_path / "raw-output.bin").read_bytes() == (
            terminal.session.output()
        )
        screen_artifact = (tmp_path / "screen.txt").read_text(encoding="utf-8")
        assert "[scrollback]" in screen_artifact
        assert "[screen]" in screen_artifact
        assert "[cursor]" in screen_artifact
