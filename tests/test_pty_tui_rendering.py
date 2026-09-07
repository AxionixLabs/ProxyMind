import json
import os
import sys
import time
from pathlib import Path

import pytest

from agent.protocol.json_value import ThawedJsonValue
from agent.protocol.json_value import freeze_json
from agent.protocol.json_value import thaw_object
from tests.support.pty import PtyKey
from tests.support.pty import TerminalHarness
from tests.support.pty import TerminalMode
from tests.support.pty import TerminalSize
from tests.support.pty import TerminalSnapshot
from tests.support.pty import spawn_terminal


pytestmark = pytest.mark.pty_acceptance


def _spawn_render_scenario(
    scenario: str,
    facts_path: Path,
    *,
    size: TerminalSize = TerminalSize(rows=24, columns=80),
) -> TerminalHarness:
    """在原生 PTY 中启动真实产品渲染场景。"""
    return spawn_terminal(
        [
            sys.executable,
            "-m",
            "tests.support.pty.tui_render_scenario",
            scenario,
            str(facts_path),
        ],
        cwd=Path.cwd(),
        env=os.environ,
        size=size,
        failure_artifact_directory=facts_path.parent / "artifacts",
    )


def _read_facts(path: Path) -> dict[str, ThawedJsonValue]:
    """读取并验证子进程发布的渲染事实。"""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return thaw_object(
        freeze_json(loaded, field_name="PTY render facts"),
        field_name="PTY render facts",
    )


def _wait_for_stage(
    path: Path,
    expected: str,
    *,
    timeout: float = 10.0,
) -> dict[str, ThawedJsonValue]:
    """等待子进程原子发布指定渲染阶段。"""
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
    raise TimeoutError(f"timed out waiting for PTY render stage {expected!r}")


def _acknowledge(path: Path, stage: str) -> None:
    """允许子进程在父进程完成当前 Screen 断言后继续。"""
    path.with_suffix(f".{stage}.ack").write_text("observed", encoding="ascii")


def _screen_archive(snapshot: TerminalSnapshot) -> str:
    """返回原生 scrollback 与可见 Screen 的单一有序文本。"""
    return "\n".join((snapshot.scrollback_text, snapshot.visible_text))


def _detail(
    facts: dict[str, ThawedJsonValue],
    name: str,
) -> ThawedJsonValue:
    """返回一个存在的具名渲染事实。"""
    details = facts.get("details")
    if not isinstance(details, dict) or name not in details:
        raise KeyError(f"PTY render fact is missing: {name}")
    return details[name]


def _assert_balanced_mode(
    modes: tuple[TerminalMode, ...],
    enabled: TerminalMode,
    disabled: TerminalMode,
    *,
    required: bool = True,
) -> None:
    """断言终端 mode 的启停数量与最终状态均已收敛。"""
    enabled_count = modes.count(enabled)
    disabled_count = modes.count(disabled)
    if required:
        assert enabled_count > 0
    assert enabled_count == disabled_count
    if enabled_count:
        assert max(index for index, mode in enumerate(modes) if mode is disabled) > max(
            index for index, mode in enumerate(modes) if mode is enabled
        )


def _assert_cursor_restored(modes: tuple[TerminalMode, ...]) -> None:
    """断言渲染期间隐藏过的光标最终重新可见。"""
    assert TerminalMode.CURSOR_HIDDEN in modes
    assert TerminalMode.CURSOR_SHOWN in modes
    assert max(
        index for index, mode in enumerate(modes)
        if mode is TerminalMode.CURSOR_SHOWN
    ) > max(
        index for index, mode in enumerate(modes)
        if mode is TerminalMode.CURSOR_HIDDEN
    )


def _assert_first_frame_golden(
    snapshot: TerminalSnapshot,
    size: TerminalSize,
) -> None:
    """核对各平台共享的首帧结构 golden。"""
    assert len(snapshot.visible_lines) == size.rows
    assert all(len(line) == size.columns for line in snapshot.visible_lines)
    nonblank = [line.rstrip() for line in snapshot.visible_lines if line.strip()]
    assert any("›" in line for line in nonblank)
    assert any("pty-model" in line for line in nonblank)
    assert any("test" in line for line in nonblank)
    assert 0 <= snapshot.cursor.row < size.rows
    assert 0 <= snapshot.cursor.column < size.columns
    assert not snapshot.cursor.hidden


def _expected_tui_width(size: TerminalSize) -> int:
    """返回 prompt_toolkit 在当前平台暴露的内容宽度。"""
    return size.columns - 1 if sys.platform == "win32" else size.columns


@pytest.mark.parametrize(
    "size",
    (
        TerminalSize(rows=24, columns=80),
        TerminalSize(rows=32, columns=120),
        TerminalSize(rows=12, columns=40),
        TerminalSize(rows=8, columns=80),
    ),
    ids=("80x24", "120x32", "narrow", "short"),
)
def test_startup_frame_matches_shared_screen_golden(
    tmp_path: Path,
    size: TerminalSize,
) -> None:
    """验证常规、窄屏和短屏启动首帧无空白、越界或隐藏光标。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_render_scenario("first_frame", facts_path, size=size) as terminal:
        facts = _wait_for_stage(facts_path, "first_frame")
        snapshot = terminal.wait_for_screen_text("›")
        _assert_first_frame_golden(snapshot, size)
        assert _detail(facts, "terminal_width") == _expected_tui_width(size)
        assert _detail(facts, "terminal_height") == size.rows
        terminal.session.wait_for_output("\x1b]0;pty-workspace\x07")
        _acknowledge(facts_path, "first_frame")
        assert terminal.wait_for_exit(timeout=10.0) == 0

        raw = terminal.diagnostics().raw_output
        modes = tuple(event.mode for event in terminal.mode_events)

    assert b"\x1b]0;pty-workspace\x07" in raw
    assert raw.rfind(b"\x1b]0;\x07") > raw.find(b"\x1b]0;pty-workspace\x07")
    _assert_balanced_mode(
        modes,
        TerminalMode.BRACKETED_PASTE_ENABLED,
        TerminalMode.BRACKETED_PASTE_DISABLED,
    )
    _assert_cursor_restored(modes)
    assert modes[-1] in {
        TerminalMode.CURSOR_SHOWN,
        TerminalMode.BRACKETED_PASTE_DISABLED,
        TerminalMode.FOCUS_REPORTING_DISABLED,
    }


def test_dynamic_layout_regions_do_not_overlap(tmp_path: Path) -> None:
    """验证正文、状态、队列和多行输入在真实 Screen 中保持独立区域。"""
    facts_path = tmp_path / "facts.json"
    size = TerminalSize(rows=18, columns=72)
    with _spawn_render_scenario("layout", facts_path, size=size) as terminal:
        facts = _wait_for_stage(facts_path, "layout")
        for marker in (
            "LAYOUT TRANSCRIPT",
            "LAYOUT STATUS",
            "LAYOUT QUEUED",
            "LAYOUT PENDING",
            "LAYOUT DRAFT",
        ):
            terminal.wait_for_screen_text(marker)
        snapshot = terminal.screen.snapshot()
        archive = _screen_archive(snapshot)
        assert archive.count("LAYOUT TRANSCRIPT") == 1
        assert archive.count("LAYOUT STATUS") == 1
        assert archive.count("LAYOUT QUEUED") == 1
        assert archive.count("LAYOUT PENDING") == 1
        assert archive.count("LAYOUT DRAFT") == 1
        assert _detail(facts, "queued_active") is True
        assert _detail(facts, "pending_active") is True
        assert 0 <= snapshot.cursor.row < size.rows
        assert 0 <= snapshot.cursor.column < size.columns
        _acknowledge(facts_path, "layout")
        assert terminal.wait_for_exit(timeout=10.0) == 0


def test_stream_retry_keeps_attempt_order_and_exactly_once_final_text(
    tmp_path: Path,
) -> None:
    """验证 delta、provider supersede 和最终正文不产生空白或重复块。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_render_scenario("stream_retry", facts_path) as terminal:
        _wait_for_stage(facts_path, "old_attempt")
        old = terminal.wait_for_screen_text("STREAM OLD attempt")
        assert _screen_archive(old).count("STREAM OLD attempt") == 1
        _acknowledge(facts_path, "old_attempt")

        settled_facts = _wait_for_stage(facts_path, "stream_settled")
        settled = terminal.wait_for_screen_text("STREAM LONGWORD")
        settled_archive = _screen_archive(settled)
        assert "STREAM OLD width marker" in settled_archive
        assert "宽" in settled_archive
        assert "🙂" in settled_archive
        document_text = _detail(settled_facts, "document_text")
        assert isinstance(document_text, str)
        assert "STREAM OLD width marker 宽🙂é" in document_text
        assert _detail(settled_facts, "scrollback_lines") > 0
        _acknowledge(facts_path, "stream_settled")

        _wait_for_stage(facts_path, "retrying")
        retrying = terminal.wait_for_screen_text("Retrying")
        retry_archive = _screen_archive(retrying)
        assert retry_archive.index("STREAM OLD attempt") < retry_archive.index(
            "Previous attempt interrupted; retrying"
        )
        assert retry_archive.strip()
        _acknowledge(facts_path, "retrying")

        _wait_for_stage(facts_path, "new_attempt")
        regenerated = terminal.wait_for_screen_text("STREAM NEW regenerated")
        regenerated_archive = _screen_archive(regenerated)
        assert regenerated_archive.index("Previous attempt") < regenerated_archive.index(
            "STREAM NEW regenerated"
        )
        _acknowledge(facts_path, "new_attempt")

        facts = _wait_for_stage(facts_path, "stream_final")
        final_snapshot = terminal.wait_for_screen_text("STREAM NEW regenerated")
        assert _detail(facts, "old_count") == 1
        assert _detail(facts, "retry_count") == 1
        assert _detail(facts, "new_count") == 1
        final_archive = _screen_archive(final_snapshot)
        assert final_archive.count("STREAM OLD attempt") == 1
        assert final_archive.count("STREAM NEW regenerated") == 1
        final_document = _detail(facts, "document_text")
        assert isinstance(final_document, str)
        assert final_document.count("STREAM USER request") == 1
        _acknowledge(facts_path, "stream_final")
        assert terminal.wait_for_exit(timeout=10.0) == 0
        modes = tuple(event.mode for event in terminal.mode_events)

    _assert_balanced_mode(
        modes,
        TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED,
        TerminalMode.SYNCHRONIZED_OUTPUT_DISABLED,
    )
    _assert_cursor_restored(modes)


def test_tool_approval_effect_and_shell_states_converge_in_place(
    tmp_path: Path,
) -> None:
    """验证活动状态消失后只保留一份工具、审批、effect 和 shell 终态。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_render_scenario("operations", facts_path) as terminal:
        _wait_for_stage(facts_path, "tool_active")
        terminal.wait_for_screen_text("Thinking")
        _acknowledge(facts_path, "tool_active")

        _wait_for_stage(facts_path, "approval_settled")
        terminal.wait_for_screen_text("echo PTY SHELL")
        _acknowledge(facts_path, "approval_settled")

        _wait_for_stage(facts_path, "shell_waiting")
        waiting = terminal.wait_for_screen_text("Waiting for background terminal")
        assert _screen_archive(waiting).count("Waiting for background terminal") == 1
        _acknowledge(facts_path, "shell_waiting")

        facts = _wait_for_stage(facts_path, "operations_final")
        final_snapshot = terminal.wait_for_screen_text("PTY EFFECT COMMITTED")
        archive = _screen_archive(final_snapshot)
        assert "Waiting for background terminal" not in final_snapshot.visible_text
        assert archive.count("PTY SHELL DONE") == 1
        assert archive.count("PTY EFFECT COMMITTED") == 1
        assert _detail(facts, "shell_result_count") == 1
        assert _detail(facts, "effect_result_count") == 1
        block_kinds = _detail(facts, "block_kinds")
        assert isinstance(block_kinds, list)
        assert "approval" in block_kinds
        assert block_kinds.count("operation") >= 3
        _acknowledge(facts_path, "operations_final")
        assert terminal.wait_for_exit(timeout=10.0) == 0
def test_resize_storm_reflows_stream_at_final_geometry(tmp_path: Path) -> None:
    """验证活动流在 resize storm 后只按最终尺寸重建且不丢 Unicode。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_render_scenario(
        "resize",
        facts_path,
        size=TerminalSize(rows=24, columns=100),
    ) as terminal:
        _wait_for_stage(facts_path, "resize_ready")
        terminal.wait_for_screen_text("RESIZE ROW 17")
        for size in (
            TerminalSize(rows=18, columns=78),
            TerminalSize(rows=10, columns=44),
            TerminalSize(rows=20, columns=90),
            TerminalSize(rows=14, columns=52),
        ):
            terminal.resize(size)
        _acknowledge(facts_path, "resize_ready")

        facts = _wait_for_stage(facts_path, "resize_final")
        snapshot = terminal.wait_for_screen_text("RESIZE ROW 17")
        final_size = TerminalSize(rows=14, columns=52)
        assert _detail(facts, "terminal_width") == _expected_tui_width(final_size)
        assert _detail(facts, "terminal_height") == 14
        assert _detail(facts, "row_zero_count") == 1
        assert _detail(facts, "row_last_count") == 1
        for row in range(final_size.rows):
            terminal.screen.cell(row, final_size.columns - 1)
        with pytest.raises(IndexError):
            terminal.screen.cell(0, final_size.columns)
        document_text = _detail(facts, "document_text")
        assert isinstance(document_text, str)
        assert "中文宽度🙂 é" in document_text
        continued_text = document_text.replace("\n  ", "")
        assert (
            "https://example.test/no-space/abcdefghijklmnopqrstuvwxyz0123456789"
            in continued_text
        )
        assert "sample.py" in document_text
        assert "new value with a longer rendered line" in document_text
        _acknowledge(facts_path, "resize_final")
        assert terminal.wait_for_exit(timeout=10.0) == 0
        modes = tuple(event.mode for event in terminal.mode_events)

    _assert_balanced_mode(
        modes,
        TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED,
        TerminalMode.SYNCHRONIZED_OUTPUT_DISABLED,
    )


def test_transcript_overlay_preserves_search_scroll_and_modes_during_resize(
    tmp_path: Path,
) -> None:
    """验证 overlay 在活动输出与 resize 中保留搜索、滚动和 raw 状态。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_render_scenario("overlay", facts_path) as terminal:
        _wait_for_stage(facts_path, "overlay_ready")
        terminal.write_user(b"\x14")
        _acknowledge(facts_path, "overlay_ready")

        _wait_for_stage(facts_path, "overlay_open")
        terminal.wait_for_screen_text("OVERLAY ROW 35")
        terminal.write_user_text("/target-2")
        terminal.send_key(PtyKey.ENTER)
        _acknowledge(facts_path, "overlay_open")

        search = _wait_for_stage(facts_path, "overlay_search")
        search_snapshot = terminal.wait_for_screen_text("target-2")
        assert "target-2" in search_snapshot.visible_text
        assert isinstance(_detail(search, "search_offset"), int)
        terminal.write_user(b"\x1b[5~")
        _acknowledge(facts_path, "overlay_search")

        live = _wait_for_stage(facts_path, "overlay_live")
        terminal.wait_for_screen_text("target-2")
        assert _detail(live, "live_offset") == _detail(live, "scrolled_offset")
        terminal.resize(TerminalSize(rows=16, columns=64))
        terminal.write_user_text("r")
        _acknowledge(facts_path, "overlay_live")

        resized = _wait_for_stage(facts_path, "overlay_resized")
        raw_snapshot = terminal.wait_for_screen_text("OVERLAY RAW 18")
        assert "target-2" in raw_snapshot.visible_text
        assert "OVERLAY LIVE RAW" not in raw_snapshot.visible_text
        final_size = TerminalSize(rows=16, columns=64)
        assert _detail(resized, "terminal_width") == _expected_tui_width(final_size)
        assert _detail(resized, "terminal_height") == 16
        assert _detail(resized, "resized_offset") == _detail(live, "live_offset")
        result_position = _detail(resized, "search_result_position")
        assert isinstance(result_position, list)
        assert result_position[1] > 0
        terminal.write_user(b"\x1b[F")
        terminal.wait_for_screen_text("OVERLAY LIVE RAW")
        terminal.write_user(b"\x14")
        _acknowledge(facts_path, "overlay_resized")
        assert terminal.wait_for_exit(timeout=10.0) == 0
        modes = tuple(event.mode for event in terminal.mode_events)

    _assert_balanced_mode(
        modes,
        TerminalMode.ALTERNATE_SCREEN_ENABLED,
        TerminalMode.ALTERNATE_SCREEN_DISABLED,
    )
    _assert_balanced_mode(
        modes,
        TerminalMode.ALTERNATE_SCROLL_ENABLED,
        TerminalMode.ALTERNATE_SCROLL_DISABLED,
    )
    _assert_balanced_mode(
        modes,
        TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED,
        TerminalMode.SYNCHRONIZED_OUTPUT_DISABLED,
        required=False,
    )


@pytest.mark.parametrize(
    "scenario",
    ("sync_cancel", "sync_exception"),
    ids=("cancel", "exception"),
)
def test_terminal_modes_restore_after_render_failure(
    tmp_path: Path,
    scenario: str,
) -> None:
    """验证取消和异常退出均关闭同步输出并恢复光标与标题。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_render_scenario(scenario, facts_path) as terminal:
        _wait_for_stage(facts_path, "sync_active")
        terminal.wait_for_modes((TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED,))
        terminal.session.wait_for_output("\x1b]0;pty-workspace\x07")
        _acknowledge(facts_path, "sync_active")
        assert terminal.wait_for_exit(timeout=10.0) != 0
        diagnostics = terminal.diagnostics()
        modes = tuple(event.mode for event in terminal.mode_events)

    _assert_balanced_mode(
        modes,
        TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED,
        TerminalMode.SYNCHRONIZED_OUTPUT_DISABLED,
    )
    _assert_cursor_restored(modes)
    assert not diagnostics.screen.cursor.hidden
    assert diagnostics.raw_output.rfind(b"\x1b]0;\x07") > diagnostics.raw_output.find(
        b"\x1b]0;pty-workspace\x07"
    )
