# -*- coding: utf-8 -*-

from mind_app.modes.support.repl_prompt import exec_status_display_label
from mind_core.prompting.box import PromptToolkitBox


def test_exec_status_display_label_empty_when_no_running_sessions() -> None:
    """没有运行会话时不生成 exec 状态行。"""
    assert exec_status_display_label({"count": 0, "items": []}) == ""
    assert exec_status_display_label({}) == ""


def test_exec_status_display_label_uses_first_command_and_extra_count() -> None:
    """多个运行会话时展示首个命令和额外数量。"""
    snapshot = {
        "count": 3,
        "items": [
            {"command": "adb logcat"},
            {"command": "npm run dev"},
            {"command": "python server.py"},
        ],
    }

    assert exec_status_display_label(snapshot) == "adb logcat · +2"


def test_exec_status_display_label_clips_long_command() -> None:
    """过长命令会被裁剪，避免撑开 prompt。"""
    snapshot = {
        "count": 1,
        "items": [{"command": "python " + ("x" * 40)}],
    }

    assert exec_status_display_label(snapshot, command_limit=12) == "python xxxx…"


def test_exec_status_display_label_uses_terminal_width() -> None:
    """按终端宽度为 exec 行预留前缀和额外数量。"""
    snapshot = {
        "count": 12,
        "items": [{"command": "python " + ("x" * 40)}],
    }

    assert exec_status_display_label(snapshot, line_width=24) == "python xxxx… · +11"


def test_prompt_message_includes_exec_status_line_when_present() -> None:
    """存在 exec 状态时 prompt 在输入行前插入状态行。"""
    message = PromptToolkitBox._render_message(
        "gpt-test",
        {"brand": "#fff", "soft": "#eee"},
        workspace_label="D:\\work",
        access_label="Approval",
        exec_status_label="adb logcat · +1",
    )

    assert (
        "<prompt.exec>exec</prompt.exec> "
        "<prompt.kicker>&#183;</prompt.kicker> "
        "<prompt.exec.command>adb logcat · +1</prompt.exec.command>\n"
    ) in message.value


def test_prompt_message_omits_exec_status_line_when_empty() -> None:
    """没有 exec 状态时 prompt 保持原结构。"""
    message = PromptToolkitBox._render_message(
        "gpt-test",
        {"brand": "#fff", "soft": "#eee"},
        workspace_label="D:\\work",
        access_label="Approval",
        exec_status_label="",
    )

    assert "<prompt.exec>" not in message.value
