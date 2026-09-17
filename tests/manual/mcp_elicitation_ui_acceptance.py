"""用原生 PTY 验收 MCP 菜单键盘操作、布局及最终终端恢复，保存实际终端单元格。"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from pydantic import JsonValue
from prompt_toolkit.utils import get_cwidth

from tests.pty import (
    TerminalEnvironment,
    TerminalHarness,
    TerminalMode,
    TerminalSize,
    spawn_terminal,
)


def capture(terminal: TerminalHarness, directory: Path, label: str) -> str:
    """保存原始输出和带样式的实际屏幕单元格，测试文本均为合成普通输入。"""
    time.sleep(0.12)
    snapshot = terminal.screen.snapshot()
    root = directory / label
    root.mkdir()
    terminal.save_failure_artifacts(root)
    cells = [
        [asdict(terminal.screen.cell(row, column)) for column in range(get_cwidth(line))]
        for row, line in enumerate(snapshot.visible_lines)
    ]
    with (root / "cells.json").open("x", encoding="utf-8") as stream:
        json.dump(cells, stream, ensure_ascii=False)
    return snapshot.visible_text


def wait(terminal: TerminalHarness, text: str) -> str:
    """等待可见画面出现完整短标签，超时保留失败画面。"""
    return terminal.wait_for_screen_text(text, timeout=12).visible_text


def selected(text: str) -> str:
    """读取当前选中行，用于核对菜单默认值和导航焦点。"""
    return next((line.strip() for line in text.splitlines() if line.lstrip().startswith("›")), "")


def run_case(directory: Path, repository: Path, columns: int, rows: int, *, no_color: bool) -> dict[str, JsonValue]:
    """通过操作系统 PTY 和真实 MCP 服务完成一次菜单矩阵，不直接调用菜单完成方法。"""
    directory.mkdir(parents=True)
    issues: list[str] = []
    screenshots: list[str] = []
    with spawn_terminal(
        [sys.executable, "-m", "tests.manual.mcp_elicitation_acceptance", "--directory", str(directory / "client"), "--repository", str(repository), "--extended"],
        cwd=repository, env=os.environ, terminal=TerminalEnvironment(no_color=no_color),
        size=TerminalSize(rows=rows, columns=columns),
        failure_artifact_directory=directory / "failure",
    ) as terminal:
        def screen(label: str) -> str:
            """保存当前稳定菜单并记录画面标识。"""
            screenshots.append(label)
            return capture(terminal, directory, label)

        def press(key: str, expected: str) -> str:
            """从真实键盘通道送入字符，等待下一步展示。"""
            offset = len(terminal.session.output())
            terminal.write_user_text(key)
            terminal.session.wait_for_output_change(offset, timeout=5)
            time.sleep(0.08)
            return wait(terminal, expected)

        wait(terminal, "Name *")
        screen("01-form")
        lines = terminal.screen.snapshot().visible_lines
        active_row = next(index for index, line in enumerate(lines) if "Name *" in line)
        inactive_row = next(index for index, line in enumerate(lines) if "Count" in line)
        active = terminal.screen.cell(active_row, 0)
        inactive = terminal.screen.cell(inactive_row, 0)
        if not no_color and active.foreground == inactive.foreground:
            issues.append("Selected row has no distinct foreground color")
        if no_color and any(
            terminal.screen.cell(row, column).foreground != "default"
            or terminal.screen.cell(row, column).background != "default"
            for row in range(rows) for column in range(columns)
        ):
            issues.append("NO_COLOR still emits colored menu cells")
        press("5", "Complete the")
        screen("02-required-error")
        press("1", "Type: string")
        terminal.write_user_text("\x1b[200~排查输入\n第二行\x1b[201~")
        wait(terminal, "第二行")
        text = screen("03-unicode-multiline")
        if "排查输入" not in text:
            issues.append("Multiline editor hides the preceding line")
        press("\x1b", "Name *")
        text = screen("04-field-cancel")
        if "(unset)" not in text:
            issues.append("Cancelled edit changed the preview")
        press("1", "Type: string")
        press("Acceptance\r", "Name *")
        press("2", "Maximum: 5")
        press("\x0199\x0b\r", "matching")
        screen("05-integer-error")
        press("\x013\x0b\r", "Name *")
        press("3", "Yes")
        text = screen("06-boolean-default")
        if "No" not in selected(text):
            issues.append("Boolean editor highlights Yes while current value is false")
        press("\x1b[A\r", "Name *")
        press("3", "Yes")
        press("2", "Name *")
        press("4", "red")
        text = screen("07-enum-default")
        if "blue" not in selected(text):
            issues.append("Enum editor highlights red while current value is blue")
        press("\x1b[A\r", "Name *")
        press("4", "red")
        press("\x1b[B\r", "Name *")
        screen("08-final-preview")
        press("5", "Choose Decline")
        screen("09-next-form")
        press("6", "Press Esc")
        press("\x1b", "(first)")
        screen("10-parallel-first")
        press("6", "(second)")
        screen("11-parallel-second")
        press("7", "Open URL")
        screen("12-url-decline")
        terminal.write_user_text("2")
        time.sleep(0.3)
        wait(terminal, "Open URL")
        screen("13-url-accept")
        terminal.write_user_text("1")
        wait(terminal, "Call will be")
        screen("14-call-cancel")
        wait(terminal, "Choose Decline after")
        screen("15-recovered")
        press("6", "Notes /")
        screen("16-extended-preview")
        press("1", "Type: string")
        terminal.write_user_text("\x01\x0b\x1b[200~排查输入\n第二行\x1b[201~")
        wait(terminal, "第二行")
        screen("17-multiline-paste")
        press("\r", "Notes /")
        press("2", "Selections:")
        press("4", "matching")
        screen("18-multiselect-required")
        press("3", "[x] storage")
        text = screen("19-multiselect-focus")
        if "storage" not in selected(text):
            issues.append("Multiselect loses the focus of the toggled choice")
        press("1", "[x] backend")
        press("2", "[x] frontend")
        press("4", "matching")
        screen("20-multiselect-limit")
        press("2", "[ ] frontend")
        press("4", "Notes /")
        press("3", "Type: number")
        press("\x01NaN\x0b\r", "matching")
        screen("21-finite-number")
        press("\x010.75\x0b\r", "Notes /")
        press("4", "Type: string")
        press("\x01\x0b\r", "Notes /")
        screen("22-empty-string")
        press("5", "Type: string")
        screen("23-long-value")
        terminal.resize(TerminalSize(rows=rows, columns=100))
        time.sleep(0.3)
        screen("24-resized-wide")
        terminal.resize(TerminalSize(rows=rows, columns=columns))
        time.sleep(0.3)
        text = screen("25-resized-back")
        if text.count("Type: string") != 1 or text.count("Long field label") != 1:
            issues.append("Resize leaves a stale field heading")
        press("\x1b", "Notes /")
        text = screen("26-extended-final")
        if "Type: string" in text or "Length: 0" in text:
            issues.append("Previous field editor remains above the form")
        terminal.write_user_text("6")
        assert terminal.wait_for_exit(timeout=15) == 0
        screen("27-exit")
        modes = tuple(event.mode for event in terminal.mode_events)
        assert TerminalMode.BRACKETED_PASTE_ENABLED in modes
        assert modes.count(TerminalMode.BRACKETED_PASTE_ENABLED) == modes.count(TerminalMode.BRACKETED_PASTE_DISABLED)
        assert not terminal.screen.snapshot().cursor.hidden
        report = json.loads((directory / "client/report.json").read_text(encoding="utf-8"))
        assert report["passed"] is True
        return {"passed": not issues, "columns": columns, "rows": rows, "no_color": no_color,
            "issues": issues, "screens": screenshots,
            "pty_pid": terminal.session.pid, "functional_passed": True, "terminal_restored": True}


def main() -> None:
    """在全新目录执行指定尺寸的终端验收并保存检查结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--columns", type=int, default=80)
    parser.add_argument("--rows", type=int, default=28)
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.exists():
        parser.error("Acceptance directory already exists")
    if args.columns <= 0 or args.rows <= 0:
        parser.error("Terminal dimensions must be positive")
    report: dict[str, JsonValue] = {"passed": False}
    try:
        report.update(run_case(directory, args.repository.resolve(), args.columns, args.rows, no_color=args.no_color))
    finally:
        if directory.is_dir():
            with (directory / "report.json").open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
