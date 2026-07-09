# -*- coding: utf-8 -*-

from mind_app.modes.support.repl_summary import (
    CommandSummary,
    command_summary_text,
    command_summary_title_parts
)


def test_command_summary_title_truncates_long_command() -> None:
    """统一命令摘要标题会裁剪过长命令。"""
    summary = CommandSummary(
        kind="Exec",
        command="python -u " + "very_long_argument_" * 20,
        suffix=" · exit 1",
        lines=()
    )

    title = "".join(
        text for text, _ in command_summary_title_parts(summary, terminal_width=60)
    )

    assert title.startswith("• Exec ")
    assert "exit 1" in title
    assert "…" in title


def test_command_summary_text_contains_output_lines() -> None:
    """统一命令摘要文本包含输出尾部行。"""
    summary = CommandSummary(
        kind="Shell",
        command="echo hi",
        lines=("hello", "world")
    )

    text = command_summary_text(summary, terminal_width=80).plain

    assert "Shell echo hi" in text
    assert "hello" in text
    assert "world" in text


def test_command_summary_title_has_hard_command_limit() -> None:
    """统一命令摘要标题在宽终端也会保持紧凑。"""
    command = "Write-Output 'non-destructive shell command started; sleeping for 60 seconds'; Start-Sleep -Seconds 60"
    summary = CommandSummary(
        kind="Exec",
        command=command,
        lines=("non-destructive shell command started; sleeping for 60 seconds",)
    )

    text = command_summary_text(summary, terminal_width=200).plain
    title = text.splitlines()[0]

    assert command not in title
    assert title.endswith("…")
    assert len(title) <= len("• Exec ") + 72


def test_command_summary_output_lines_are_clipped() -> None:
    """统一命令摘要正文行按可用宽度裁剪。"""
    summary = CommandSummary(
        kind="Exec",
        command="task",
        lines=("x" * 200,)
    )

    text = command_summary_text(summary, terminal_width=50).plain
    output = text.splitlines()[1].strip()

    assert output.endswith("…")
    assert len(output) <= 48
