# -*- coding: utf-8 -*-

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import mind_app.modes.support.repl_shell as repl_shell
from mind_app.modes.support.repl_shell import (
    SHELL_PANEL_STYLE,
    ShellPanelRun,
    ShellRunResult,
    _split_direct_command,
    blocked_interactive_shell_command,
    parse_shell_escape,
    run_shell_escape,
    run_shell_command_panel,
    shell_panel_status_suffix,
    shell_panel_summary_command_text,
    shell_panel_summary_lines,
)


def run_async(value: object) -> object:
    """同步运行异步测试目标。"""
    return asyncio.run(value)


def test_shell_panel_stderr_uses_neutral_style() -> None:
    """实时 stderr 使用中性色而不是错误红。"""
    styles = dict(SHELL_PANEL_STYLE.style_rules)

    assert styles["shell.stderr"] == "#B8C1CB"


def test_parse_shell_escape_command() -> None:
    """感叹号输入解析为本地 shell 命令。"""
    parsed = parse_shell_escape("!adb devices")

    assert parsed is not None
    assert parsed.enter_shell is False
    assert parsed.command == "adb devices"


def test_parse_shell_escape_enter_shell() -> None:
    """单独感叹号解析为进入本地 shell。"""
    parsed = parse_shell_escape("!")

    assert parsed is not None
    assert parsed.enter_shell is True
    assert parsed.command == ""


def test_blocked_interactive_shell_command_detects_ssh() -> None:
    """交互式 shell 命令会被识别为屏蔽对象。"""
    assert blocked_interactive_shell_command("ssh -p 2033 test@192.168.2.81") == "ssh"
    assert blocked_interactive_shell_command("ssh.exe test@example.com") == "ssh"


def test_run_shell_escape_blocks_interactive_command(monkeypatch) -> None:
    """命中屏蔽规则时不启动 shell 面板。"""
    rendered: list[tuple[str, str]] = []
    application = SimpleNamespace(
        viewport=SimpleNamespace(width=100, height=24),
        emit=lambda view: None,
    )

    async def fail_panel(*args, **kwargs):
        raise AssertionError("shell panel should not start")

    def capture_render(application_value, command: str, name: str) -> None:
        assert application_value is application
        rendered.append((command, name))

    monkeypatch.setattr(repl_shell, "run_shell_command_panel", fail_panel)
    monkeypatch.setattr(repl_shell, "render_blocked_interactive_shell_command", capture_render)

    assert run_async(
        run_shell_escape(application, "!ssh -p 2033 test@192.168.2.81")
    ) is True
    assert rendered == [("ssh -p 2033 test@192.168.2.81", "ssh")]


def test_run_shell_command_panel_returns_short_output(tmp_path: Path) -> None:
    """短命令输出会被收集到最终结果。"""
    script = tmp_path / "hello.py"
    script.write_text("print('hello-shell')\n", encoding="utf-8")

    result = run_async(
        run_shell_command_panel(
            f"python {script}",
            show_panel=False,
            timeout_sec=5,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello-shell"


def test_run_shell_command_panel_keeps_shell_semantics() -> None:
    """shell escape 仍支持 shell 组合语法。"""
    result = run_async(
        run_shell_command_panel(
            "echo shell-a && echo shell-b",
            show_panel=False,
            timeout_sec=5,
        )
    )

    assert result.exit_code == 0
    assert "shell-a" in result.stdout
    assert "shell-b" in result.stdout


def test_run_shell_command_panel_does_not_wait_for_inherited_pipe(tmp_path: Path) -> None:
    """父进程退出后不会等待持有管道的子进程自然退出。"""
    script = tmp_path / "spawn_holder.py"
    script.write_text(
        "\n".join([
            "import subprocess",
            "import sys",
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], stdout=sys.stdout, stderr=sys.stderr)",
            "print('parent-finished')",
            "",
        ]),
        encoding="utf-8",
    )

    started = time.monotonic()
    result = run_async(
        run_shell_command_panel(
            f"python {script}",
            show_panel=False,
            timeout_sec=5,
        )
    )
    elapsed = time.monotonic() - started

    assert result.exit_code == 0
    assert "parent-finished" in result.stdout
    assert elapsed < 2.0


def test_run_shell_command_panel_times_out_long_command() -> None:
    """超时命令会被终止并返回超时标记。"""
    result = run_async(
        run_shell_command_panel(
            "python -c \"import time; time.sleep(5)\"",
            show_panel=False,
            timeout_sec=1,
        )
    )

    assert result.exit_code != 0
    assert result.timed_out is True


def test_run_shell_command_panel_returns_start_failure(monkeypatch) -> None:
    """进程启动失败会返回 shell 结果而不是抛出到 REPL。"""
    def fail_start(**kwargs):
        raise OSError("start failed")

    monkeypatch.setattr(repl_shell.ShellPanelRun, "start", fail_start)

    result = run_async(
        run_shell_command_panel(
            "bad-start",
            show_panel=False,
            timeout_sec=1,
        )
    )

    assert result.exit_code == 1
    assert result.stderr == "start failed"
    assert result.display_lines == ("start failed",)


def test_split_direct_command_preserves_argument_text() -> None:
    """直连命令拆参不裁剪用户传入的参数内容。"""
    assert _split_direct_command('python ""') == ["python", ""]
    assert _split_direct_command('python "  spaced  "') == ["python", "  spaced  "]


def test_shell_panel_summary_lines_use_display_order() -> None:
    """最终摘要使用面板接收顺序而不是 stdout/stderr 拼接顺序。"""
    result = ShellRunResult(
        exit_code=0,
        stdout="stdout-late\n",
        stderr="stderr-first\n",
        display_lines=("stderr-first", "stdout-late"),
    )

    assert shell_panel_summary_lines(result) == ["stderr-first", "stdout-late"]


def test_shell_panel_summary_lines_omit_success_no_output() -> None:
    """成功且无输出时最终摘要不显示 no output。"""
    result = ShellRunResult(exit_code=0, stdout="", stderr="")

    assert shell_panel_summary_lines(result) == []


def test_shell_panel_status_suffix() -> None:
    """标题状态后缀覆盖停止、退出码和超时。"""
    assert shell_panel_status_suffix(
        ShellRunResult(exit_code=130, stdout="", stderr="", stopped=True)
    ) == " · stop"
    assert shell_panel_status_suffix(
        ShellRunResult(exit_code=1, stdout="", stderr="")
    ) == " · exit 1"
    assert shell_panel_status_suffix(
        ShellRunResult(exit_code=-1, stdout="", stderr="", timed_out=True)
    ) == " · timeout"


def test_shell_panel_summary_command_text_keeps_suffix_space() -> None:
    """长命令标题裁剪时给状态后缀预留空间。"""
    command = "adb logcat -v threadtime -s ActivityTaskManager PackageManager"
    suffix = " · stop"
    text = shell_panel_summary_command_text(command, suffix=suffix, terminal_width=42)

    assert text.endswith("...")
    assert len("• Shell " + text + suffix) <= 42


def test_shell_panel_run_buffers_partial_lines() -> None:
    """chunk 切开的半行会在展示缓冲中合并。"""
    run = ShellPanelRun.__new__(ShellPanelRun)
    run.stdout_text = ""
    run.stderr_text = ""
    run.output_lines = []
    run.pending_lines = {"stdout": "", "stderr": ""}

    run._append_display_text("stderr", "*", "class:shell.stderr")
    run._append_display_text(
        "stderr",
        " daemon not running; starting now at tcp:5037\n",
        "class:shell.stderr",
    )

    assert [line for _, line in run.output_lines] == [
        "* daemon not running; starting now at tcp:5037"
    ]


def test_shell_panel_live_lines_match_summary_shape() -> None:
    """流式面板标题和输出缩进与最终摘要保持一致。"""
    run = ShellPanelRun.__new__(ShellPanelRun)
    run.command = "adb devices"
    run.started_at = time.monotonic()
    run.exit_code = None
    run.timed_out = False
    run.stopped = False
    run.output_lines = [("class:shell.stdout", "List of devices attached")]
    run.lock = threading.RLock()

    parts = run.live_lines(height=8)
    texts = [text for _, text in parts]

    assert "Shell" in texts
    assert "  " in texts
    assert "List of devices attached" in texts
    assert not any("running ·" in text for text in texts)
