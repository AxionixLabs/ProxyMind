import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

from agent.protocol.json_value import ThawedJsonValue
from agent.protocol.json_value import freeze_json
from agent.protocol.json_value import thaw_object
from tests.support.pty import PtyKey
from tests.support.pty import TerminalEnvironment
from tests.support.pty import TerminalHarness
from tests.support.pty import TerminalMode
from tests.support.pty import TerminalSize
from tests.support.pty import TerminalSnapshot
from tests.support.pty import spawn_terminal


pytestmark = pytest.mark.pty_acceptance

_TERMINAL_SIZE = TerminalSize(rows=28, columns=100)
_TRUECOLOR_TERMINAL = TerminalEnvironment(
    term="xterm-256color",
    colorterm="truecolor",
)
_ANSI256_TERMINAL = TerminalEnvironment(
    term="xterm-256color",
    colorterm="",
)
_ANSI16_TERMINAL = TerminalEnvironment(
    term="xterm-color",
    colorterm="",
)
_NO_COLOR_TERMINAL = TerminalEnvironment(
    term="xterm-256color",
    colorterm="truecolor",
    no_color=True,
)
_CONCRETE_COLOR_SGR = re.compile(
    rb"\x1b\[[0-9;:]*(?:(?:3[0-7]|4[0-7]|9[0-7]|10[0-7])m|(?:38|48)[;:])"
)


def _spawn_color_scenario(
    scenario: str,
    theme: str,
    facts_path: Path,
    terminal: TerminalEnvironment,
) -> TerminalHarness:
    """在原生 PTY 中启动真实产品颜色场景。"""
    return spawn_terminal(
        [
            sys.executable,
            "-m",
            "tests.support.pty.tui_color_scenario",
            scenario,
            theme,
            str(facts_path),
        ],
        cwd=Path.cwd(),
        env=os.environ,
        size=_TERMINAL_SIZE,
        terminal=terminal,
        failure_artifact_directory=facts_path.parent / "artifacts",
    )


def _read_facts(path: Path) -> dict[str, ThawedJsonValue]:
    """读取并验证子进程发布的颜色事实。"""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return thaw_object(
        freeze_json(loaded, field_name="PTY color facts"),
        field_name="PTY color facts",
    )


def _wait_for_stage(
    path: Path,
    expected: str,
    *,
    timeout: float = 10.0,
) -> dict[str, ThawedJsonValue]:
    """等待子进程原子发布指定颜色阶段。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                facts = _read_facts(path)
            except (OSError, json.JSONDecodeError):
                facts = None
            if facts is not None and facts.get("stage") == expected:
                return facts
        time.sleep(0.01)
    raise TimeoutError(f"timed out waiting for PTY color stage {expected!r}")


def _acknowledge(path: Path, stage: str) -> None:
    """允许子进程在父进程完成当前 Screen 断言后继续。"""
    path.with_suffix(f".{stage}.ack").write_text("observed", encoding="ascii")


def _details(
    facts: dict[str, ThawedJsonValue],
) -> dict[str, ThawedJsonValue]:
    """返回经过结构校验的颜色场景明细。"""
    details = facts.get("details")
    if not isinstance(details, dict):
        raise TypeError("PTY color details must be an object")
    return details


def _screen_has_concrete_color(
    terminal: TerminalHarness,
    snapshot: TerminalSnapshot,
) -> bool:
    """判断当前可见非空单元格是否实际携带前景或背景色。"""
    for row, line in enumerate(snapshot.visible_lines):
        for column in range(len(line)):
            cell = terminal.screen.cell(row, column)
            if not cell.data.strip():
                continue
            if cell.foreground != "default" or cell.background != "default":
                return True
    return False


def _assert_style_color_contract(
    details: dict[str, ThawedJsonValue],
    *,
    expect_color: bool,
) -> None:
    """核对 Application 的语义样式是否遵守有效色深。"""
    styles = details.get("styles_initial")
    if not isinstance(styles, dict):
        raise TypeError("PTY color styles must be an object")
    concrete_colors = 0
    for style in styles.values():
        if not isinstance(style, dict):
            raise TypeError("PTY color style must be an object")
        foreground = style.get("foreground")
        background = style.get("background")
        if foreground != "default" or background != "default":
            concrete_colors += 1
    assert (concrete_colors > 0) is expect_color


def _assert_modes_restored(terminal: TerminalHarness) -> None:
    """核对退出后 focus、paste、光标和同步输出模式均已恢复。"""
    modes = tuple(event.mode for event in terminal.mode_events)
    for enabled, disabled in (
        (
            TerminalMode.FOCUS_REPORTING_ENABLED,
            TerminalMode.FOCUS_REPORTING_DISABLED,
        ),
        (
            TerminalMode.BRACKETED_PASTE_ENABLED,
            TerminalMode.BRACKETED_PASTE_DISABLED,
        ),
        (
            TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED,
            TerminalMode.SYNCHRONIZED_OUTPUT_DISABLED,
        ),
    ):
        assert modes.count(enabled) == modes.count(disabled)
    assert TerminalMode.CURSOR_HIDDEN in modes
    assert TerminalMode.CURSOR_SHOWN in modes
    assert modes.index(TerminalMode.CURSOR_HIDDEN) < max(
        index
        for index, mode in enumerate(modes)
        if mode is TerminalMode.CURSOR_SHOWN
    )


@pytest.mark.parametrize(
    (
        "terminal_environment",
        "theme",
        "expected_level",
        "expected_depth",
        "expect_color",
    ),
    (
        pytest.param(
            _TRUECOLOR_TERMINAL,
            "dark",
            "truecolor",
            "DEPTH_24_BIT",
            True,
            id="truecolor-dark",
        ),
        pytest.param(
            _TRUECOLOR_TERMINAL,
            "light",
            "truecolor",
            "DEPTH_24_BIT",
            True,
            id="truecolor-light",
        ),
        pytest.param(
            _ANSI256_TERMINAL,
            "dark",
            "ansi256",
            "DEPTH_8_BIT",
            True,
            id="ansi256-dark",
        ),
        pytest.param(
            _ANSI256_TERMINAL,
            "light",
            "ansi256",
            "DEPTH_8_BIT",
            True,
            id="ansi256-light",
        ),
        pytest.param(
            _ANSI16_TERMINAL,
            "dark",
            "ansi16",
            "DEPTH_4_BIT",
            True,
            id="ansi16-dark",
        ),
        pytest.param(
            _ANSI16_TERMINAL,
            "light",
            "ansi16",
            "DEPTH_4_BIT",
            True,
            id="ansi16-light",
        ),
        pytest.param(
            _NO_COLOR_TERMINAL,
            "dark",
            "none",
            "DEPTH_1_BIT",
            False,
            id="no-color-dark",
        ),
        pytest.param(
            _NO_COLOR_TERMINAL,
            "light",
            "none",
            "DEPTH_1_BIT",
            False,
            id="no-color-light",
        ),
    ),
)
def test_real_tui_color_matrix_preserves_screen_and_business_facts(
    tmp_path: Path,
    terminal_environment: TerminalEnvironment,
    theme: str,
    expected_level: str,
    expected_depth: str,
    expect_color: bool,
) -> None:
    """验证色深和主题矩阵在真实 TUI 全流程中保持可读与无泄漏。"""
    facts_path = tmp_path / "facts.json"
    artifacts = tmp_path / "artifacts"
    with _spawn_color_scenario(
        "full",
        theme,
        facts_path,
        terminal_environment,
    ) as terminal:
        try:
            input_facts = _wait_for_stage(facts_path, "input_ready")
            input_details = _details(input_facts)
            assert input_details["raw_color_level"] == expected_level
            assert input_details["effective_color_level"] == expected_level
            assert input_details["color_depth"] == expected_depth
            _assert_style_color_contract(
                input_details,
                expect_color=expect_color,
            )
            terminal.wait_for_screen_text("color-model")
            terminal.write_user_text("/f")
            _acknowledge(facts_path, "input_ready")

            completion_facts = _wait_for_stage(facts_path, "completion")
            completion = terminal.wait_for_screen_text("fork")
            assert _details(completion_facts)["completion_open"] is True
            assert _screen_has_concrete_color(terminal, completion) is expect_color
            terminal.send_key(PtyKey.ESCAPE)
            _acknowledge(facts_path, "completion")

            turn_facts = _wait_for_stage(facts_path, "turn_surfaces")
            turn_snapshot = terminal.wait_for_screen_text("COLOR PENDING STEER")
            assert "COLOR REJECTED STEER" in turn_snapshot.visible_text
            assert "COLOR QUEUED MESSAGE" in turn_snapshot.visible_text
            assert "Retrying" in turn_snapshot.visible_text
            turn_details = _details(turn_facts)
            assert turn_details["pending_active"] is True
            assert turn_details["rejected_active"] is True
            assert turn_details["queued_active"] is True
            _acknowledge(facts_path, "turn_surfaces")

            diff_facts = _wait_for_stage(facts_path, "diff")
            terminal.wait_for_screen_text("COLOR DIFF ADD")
            assert _details(diff_facts)["diff_rendered"] is True
            _acknowledge(facts_path, "diff")

            initial_facts = _wait_for_stage(facts_path, "approval_initial")
            terminal.wait_for_screen_text("Delete color record")
            assert _details(initial_facts)["approval_selected_index"] == 0
            terminal.write_user(b"\x1b[B")
            _acknowledge(facts_path, "approval_initial")

            moved_facts = _wait_for_stage(facts_path, "approval_moved")
            assert _details(moved_facts)["approval_moved_index"] == 1
            terminal.send_key(PtyKey.ENTER)
            _acknowledge(facts_path, "approval_moved")

            _wait_for_stage(facts_path, "overlay_ready")
            terminal.write_user(b"\x14")
            _acknowledge(facts_path, "overlay_ready")

            _wait_for_stage(facts_path, "overlay_open")
            terminal.wait_for_screen_text("COLOR OVERLAY ROW")
            terminal.write_user_text("/match-2")
            terminal.send_key(PtyKey.ENTER)
            _acknowledge(facts_path, "overlay_open")

            search_facts = _wait_for_stage(facts_path, "overlay_search")
            search_position = _details(search_facts)["overlay_search_position"]
            assert isinstance(search_position, list)
            assert search_position[1] > 0
            terminal.write_user_text("r")
            _acknowledge(facts_path, "overlay_search")

            raw_facts = _wait_for_stage(facts_path, "overlay_raw")
            terminal.wait_for_screen_text("COLOR RAW ROW")
            raw_details = _details(raw_facts)
            assert raw_details["overlay_raw_mode"] is True
            assert raw_details["styles_final"] == raw_details["styles_initial"]
            terminal.write_user(b"\x14")
            _acknowledge(facts_path, "overlay_raw")

            assert terminal.wait_for_exit(timeout=10.0) == 0
            final_facts = _read_facts(facts_path)
            assert final_facts["stage"] == "complete"
            assert _details(final_facts)["probe_calls"] == 1
            terminal.session.wait_for_output("PTY RESET SENTINEL")
            raw_output = terminal.diagnostics().raw_output
            assert bool(_CONCRETE_COLOR_SGR.search(raw_output)) is expect_color
            _assert_modes_restored(terminal)
        finally:
            terminal.save_failure_artifacts(artifacts)


@pytest.mark.parametrize(
    ("scenario", "theme", "expected_exit"),
    (
        pytest.param("cancel", "partial", 23, id="cancel-partial-theme"),
        pytest.param("failure", "unknown", 24, id="failure-unknown-theme"),
    ),
)
def test_real_tui_color_exit_restores_terminal_modes(
    tmp_path: Path,
    scenario: str,
    theme: str,
    expected_exit: int,
) -> None:
    """验证着色帧取消或异常退出后终端样式与模式均收敛。"""
    facts_path = tmp_path / "facts.json"
    artifacts = tmp_path / "artifacts"
    with _spawn_color_scenario(
        scenario,
        theme,
        facts_path,
        _TRUECOLOR_TERMINAL,
    ) as terminal:
        try:
            facts = _wait_for_stage(facts_path, "exit_styled")
            snapshot = terminal.wait_for_screen_text(f"COLOR {scenario.upper()} EXIT")
            assert _screen_has_concrete_color(terminal, snapshot)
            assert _details(facts)["effective_color_level"] == "truecolor"
            _acknowledge(facts_path, "exit_styled")
            assert terminal.wait_for_exit(timeout=10.0) == expected_exit
            terminal.session.wait_for_output("PTY RESET SENTINEL")
            final_facts = _read_facts(facts_path)
            assert _details(final_facts)["probe_calls"] == 1
            _assert_modes_restored(terminal)
        finally:
            terminal.save_failure_artifacts(artifacts)
