# -*- coding: utf-8 -*-

from mind_app.modes.support.repl_ps import (
    exec_session_summary_lines,
    exec_session_summary_title_parts,
    render_exec_session_menu
)


def test_exec_session_menu_truncates_long_command() -> None:
    """运行中命令菜单会裁剪过长命令。"""
    long_command = "python -u " + "very_long_argument_" * 20
    rendered = render_exec_session_menu(
        [
            {
                "session_id": "exec_test",
                "pid"       : 1234,
                "command"   : long_command,
            }
        ],
        selected=0,
    )

    text = "".join(part for _, part in rendered)

    assert "very_long_argument_" in text
    assert long_command not in text
    assert "…" in text


def test_exec_session_menu_shows_at_most_eight_items() -> None:
    """运行中命令菜单最多展示 8 条。"""
    sessions = [
        {
            "session_id": f"exec_{index}",
            "pid"       : 1000 + index,
            "command"   : f"command-{index}",
        }
        for index in range(12)
    ]

    rendered = render_exec_session_menu(sessions, selected=10)
    text = "".join(part for _, part in rendered)

    assert "showing=5-12" in text
    assert "command-3" not in text
    assert "command-4" in text
    assert "command-11" in text
    assert text.count(" pid=") == 8


def test_exec_session_summary_uses_final_output_lines() -> None:
    """exec_command 摘要展示最终输出尾部。"""
    snapshot = {
        "command"     : "python task.py",
        "status"      : "exited",
        "exit_code"   : 0,
        "output_lines": ["line-1", "line-2"],
    }

    assert exec_session_summary_lines(snapshot) == ["line-1", "line-2"]

    title = "".join(
        text for text, _ in exec_session_summary_title_parts(snapshot, terminal_width=80)
    )
    assert "Exec" in title
    assert "python task.py" in title
    assert "exit" not in title


def test_exec_session_summary_marks_nonzero_exit() -> None:
    """exec_command 非零退出会在摘要标题展示退出码。"""
    snapshot = {
        "command"     : "python fail.py",
        "status"      : "exited",
        "exit_code"   : 7,
        "output_lines": [],
    }

    title = "".join(
        text for text, _ in exec_session_summary_title_parts(snapshot, terminal_width=80)
    )

    assert "exit 7" in title
    assert exec_session_summary_lines(snapshot) == ["exec_command exited with code 7"]
