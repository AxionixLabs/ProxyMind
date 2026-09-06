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
from tests.support.pty import TerminalSize
from tests.support.pty import spawn_terminal


pytestmark = pytest.mark.pty_acceptance


def _spawn_tui(scenario: str, facts_path: Path):
    """在真实 PTY 中启动产品 TUI 测试场景。"""
    return spawn_terminal(
        [
            sys.executable,
            "-m",
            "tests.support.pty.tui_scenario",
            scenario,
            str(facts_path),
        ],
        cwd=Path.cwd(),
        env=os.environ,
        size=TerminalSize(rows=24, columns=100),
    )


def _read_facts(path: Path) -> dict[str, ThawedJsonValue]:
    """读取并校验子进程发布的场景事实。"""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return thaw_object(freeze_json(loaded, field_name="PTY TUI facts"), field_name="PTY TUI facts")


def _wait_for_stage(
    path: Path,
    expected: str,
    *,
    timeout: float = 10.0,
) -> dict[str, ThawedJsonValue]:
    """等待子进程原子发布指定业务阶段。"""
    deadline = time.monotonic() + timeout
    while True:
        if path.exists():
            try:
                facts = _read_facts(path)
            except (OSError, json.JSONDecodeError):
                facts = None
            if facts is not None and facts.get("stage") == expected:
                return facts
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for PTY TUI stage {expected!r}")
        time.sleep(0.01)


def _submissions(facts: dict[str, ThawedJsonValue]) -> list[dict[str, ThawedJsonValue]]:
    """返回经过结构校验的提交事实列表。"""
    submissions = facts.get("submissions")
    if not isinstance(submissions, list):
        raise TypeError("PTY TUI submissions must be a list")
    result: list[dict[str, ThawedJsonValue]] = []
    for submission in submissions:
        if not isinstance(submission, dict):
            raise TypeError("PTY TUI submission must be an object")
        result.append(submission)
    return result


def _details(facts: dict[str, ThawedJsonValue]) -> dict[str, ThawedJsonValue]:
    """返回经过结构校验的场景明细。"""
    details = facts.get("details")
    if not isinstance(details, dict):
        raise TypeError("PTY TUI details must be an object")
    return details


@pytest.mark.parametrize(
    ("scenario", "key"),
    (
        pytest.param("idle_enter", PtyKey.ENTER, id="enter"),
        pytest.param("idle_tab", PtyKey.TAB, id="tab"),
    ),
)
def test_idle_submit_keys_deliver_exactly_once(
    tmp_path: Path,
    scenario: str,
    key: PtyKey,
) -> None:
    """验证空闲 Enter 和普通文本 Tab 都只形成一次输入事实。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui(scenario, facts_path) as terminal:
        terminal.wait_for_screen_text(f"PTY TUI READY {scenario}")
        terminal.write_user_text("hello from PTY")
        terminal.send_key(key)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    assert facts["stage"] == "complete"
    assert [item["value"] for item in _submissions(facts)] == [
        "hello from PTY"
    ]


def test_tab_completion_precedes_submission(tmp_path: Path) -> None:
    """验证 slash 候选优先于 Tab 提交并由后续 Enter 分派。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("completion_tab", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY completion_tab")
        terminal.write_user_text("/f")
        terminal.send_key(PtyKey.TAB)
        _wait_for_stage(facts_path, "completion_applied")
        terminal.wait_for_screen_text("/fork")
        terminal.send_key(PtyKey.ENTER)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    submissions = _submissions(facts)
    assert len(submissions) == 1
    assert submissions[0]["value"] == "/fork"


@pytest.mark.parametrize(
    ("scenario", "query", "candidate", "expected"),
    (
        ("completion_skill", "$bro", "browser", "$browser "),
        (
            "completion_file",
            "@tui_scen",
            "tui_scenario.py",
            f"{Path('tests/support/pty/tui_scenario.py')} ",
        ),
    ),
)
def test_tab_prioritizes_skill_and_file_completion(
    tmp_path: Path,
    scenario: str,
    query: str,
    candidate: str,
    expected: str,
) -> None:
    """验证 skill 和文件候选打开时 Tab 只应用候选。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui(scenario, facts_path) as terminal:
        terminal.wait_for_screen_text(f"PTY TUI READY {scenario}")
        terminal.write_user_text(query)
        terminal.wait_for_screen_text(candidate)
        terminal.send_key(PtyKey.TAB)
        _wait_for_stage(facts_path, "completion_applied")
        terminal.wait_for_screen_text(expected.strip())
        terminal.send_key(PtyKey.ENTER)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    assert [item["value"] for item in _submissions(facts)] == [expected.strip()]


def test_active_enter_steers_and_tab_queues(tmp_path: Path) -> None:
    """验证活动 Turn 中 Enter、Tab 和 Shell Tab 的所有权互不混淆。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("active_inputs", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY active_inputs")
        _wait_for_stage(facts_path, "accepting")
        terminal.write_user_text("steer through enter")
        terminal.send_key(PtyKey.ENTER)
        terminal.wait_for_screen_text("steer through enter")

        terminal.write_user_text("queue through tab")
        terminal.send_key(PtyKey.TAB)
        terminal.wait_for_screen_text("Queued follow-up inputs")

        terminal.write_user_text("!echo should-not-run")
        terminal.send_key(PtyKey.TAB)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    submissions = _submissions(facts)
    assert [(item["value"], item["queue_only"]) for item in submissions] == [
        ("steer through enter", False),
        ("queue through tab", True),
        ("! echo should-not-run", True),
    ]
    assert submissions[-1]["shell_mode"] is True
    details = _details(facts)
    assert details["steer_request_count"] == 1
    assert details["interrupt_request_count"] == 0
    assert details["queued_active"] is True
    assert details["pending_active"] is False


def test_enter_queues_when_turn_is_not_steerable(tmp_path: Path) -> None:
    """验证中断阶段的真实 Enter 不会误发 steer。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("non_steer_enter", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY non_steer_enter")
        _wait_for_stage(facts_path, "accepting")
        queued_text = "next\nturn🙂\n" + ("q" * 8192)
        terminal.write_user(
            b"\x1b[200~" + queued_text.encode("utf-8") + b"\x1b[201~"
        )
        terminal.wait_for_screen_text("[Pasted Content")
        terminal.send_key(PtyKey.ENTER)
        _wait_for_stage(facts_path, "queued")
        terminal.wait_for_screen_text("Queued follow-up inputs")
        facts_path.with_suffix(".ack").write_text("screen-observed", encoding="ascii")

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    submissions = _submissions(facts)
    assert [(item["value"], item["queue_only"]) for item in submissions] == [
        (queued_text, False)
    ]
    assert submissions[0]["paste_values"] == [queued_text]
    assert submissions[0]["attachments"] == [
        {"kind": "image", "name": "queued.png"}
    ]
    assert submissions[0]["extras"] == {"source": "selection"}
    details = _details(facts)
    assert details["steer_request_count"] == 0
    assert details["interrupt_request_count"] == 1
    assert details["queued_active"] is True


def test_ctrl_c_with_draft_only_clears_draft(tmp_path: Path) -> None:
    """验证活动 Turn 首次 Ctrl-C 优先清草稿且不调用中断。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("ctrl_c_draft", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY ctrl_c_draft")
        _wait_for_stage(facts_path, "accepting")
        terminal.write_user_text("unfinished draft")
        terminal.wait_for_screen_text("unfinished draft")
        terminal.send_key(PtyKey.CTRL_C)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    details = _details(facts)
    assert details["draft_cleared"] is True
    assert details["interrupt_invocations"] == 0


@pytest.mark.parametrize(
    "scenario",
    ("ctrl_c_early", "ctrl_c_late", "ctrl_c_stream_closed"),
)
def test_double_ctrl_c_converges_across_turn_phases(
    tmp_path: Path,
    scenario: str,
) -> None:
    """验证启动前、终态后和断流阶段的 Ctrl-C 均幂等收敛。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui(scenario, facts_path) as terminal:
        terminal.wait_for_screen_text(f"PTY TUI READY {scenario}")
        _wait_for_stage(facts_path, "accepting")
        terminal.send_key(PtyKey.CTRL_C)
        terminal.wait_for_screen_text("again to exit")
        _wait_for_stage(facts_path, "interrupted_once")
        terminal.send_key(PtyKey.CTRL_C)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    details = _details(facts)
    assert details["exit_kind"] == "interrupt"
    assert details["interrupt_invocations"] == 2
    expected_remote_count = 0 if scenario == "ctrl_c_late" else 1
    assert details["interrupt_request_count"] == expected_remote_count
    if scenario == "ctrl_c_early":
        assert details["request_attempts_before_turn_started"] == 1


def test_ctrl_c_confirmation_expiry_rearms_first_press(tmp_path: Path) -> None:
    """验证确认超时后的 Ctrl-C 重新进入首次按键语义。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("ctrl_c_expiry", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY ctrl_c_expiry")
        _wait_for_stage(facts_path, "accepting")
        terminal.send_key(PtyKey.CTRL_C)
        terminal.wait_for_screen_text("again to exit")
        _wait_for_stage(facts_path, "expired", timeout=6.0)
        terminal.send_key(PtyKey.CTRL_C)
        _wait_for_stage(facts_path, "rearmed")
        terminal.wait_for_screen_text("again to exit")
        assert not terminal.session.output_closed
        terminal.send_key(PtyKey.CTRL_C)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    details = _details(facts)
    assert details["exit_kind"] == "interrupt"
    assert details["interrupt_invocations"] == 3
    assert details["interrupt_request_count"] == 1


def test_edit_last_queued_message_restores_once(tmp_path: Path) -> None:
    """验证队尾消息删除、恢复和编辑后 Enter 重交付均恰好一次。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("queue_edit", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY queue_edit")
        _wait_for_stage(facts_path, "accepting")
        terminal.write_user_text("edit once")
        terminal.send_key(PtyKey.TAB)
        terminal.wait_for_screen_text("Queued follow-up inputs")
        _wait_for_stage(facts_path, "queued")
        terminal.write_user(b"\x1b[1;2D")
        _wait_for_stage(facts_path, "restored")
        terminal.write_user_text(" revised")
        terminal.send_key(PtyKey.ENTER)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    submissions = _submissions(facts)
    assert [(item["editable_text"], item["queue_only"]) for item in submissions] == [
        ("edit once", True),
        ("edit once revised", False),
    ]
    details = _details(facts)
    assert details["queued_active"] is False
    assert details["steer_request_count"] == 1


def test_interrupt_restores_all_input_layers_in_order(tmp_path: Path) -> None:
    """验证真实 Ctrl-C 后各输入层恢复到编辑器的顺序和唯一性。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("interrupt_restore_order", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY interrupt_restore_order")
        _wait_for_stage(facts_path, "accepting")
        terminal.send_key(PtyKey.CTRL_C)
        terminal.wait_for_screen_text("again to exit")
        _wait_for_stage(facts_path, "interrupted")
        terminal.write_user_text("current draft")
        _wait_for_stage(facts_path, "restored")
        terminal.wait_for_screen_text("current draft")
        facts_path.with_suffix(".ack").write_text("screen-observed", encoding="ascii")

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    details = _details(facts)
    assert details["restored_text"] == (
        "rejected steer\npending steer\ntab follow up\ncurrent draft"
    )
    assert details["pending_active"] is False
    assert details["rejected_active"] is False
    assert details["queued_active"] is False


def test_nested_surfaces_consume_keys_before_composer(tmp_path: Path) -> None:
    """验证补全和记录页按键不会改写其下方主输入草稿。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("nested_surfaces", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY nested_surfaces")
        terminal.write_user_text("/f")
        terminal.wait_for_screen_text("fork")
        _wait_for_stage(facts_path, "completion_open")
        terminal.send_key(PtyKey.ESCAPE)
        _wait_for_stage(facts_path, "completion_closed")
        terminal.write_user(b"\x14")
        _wait_for_stage(facts_path, "transcript_open")
        terminal.wait_for_screen_text("T R A N S C R I P T")
        terminal.write_user_text("/needle")
        _wait_for_stage(facts_path, "transcript_search")
        terminal.wait_for_screen_text("/ needle")
        terminal.send_key(PtyKey.ESCAPE)
        _wait_for_stage(facts_path, "transcript_search_closed")
        terminal.write_user(b"\x14")
        _wait_for_stage(facts_path, "surfaces_consumed")
        facts_path.with_suffix(".ack").write_text(
            "surfaces-observed",
            encoding="ascii",
        )

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    details = _details(facts)
    assert details["draft_after_completion"] == "/f"
    assert details["draft_during_transcript"] == "/f"
    assert details["draft_after_transcript"] == "/f"


def test_approval_surface_consumes_key_before_composer(tmp_path: Path) -> None:
    """验证审批快捷键只形成决策且保留下方主输入草稿。"""
    facts_path = tmp_path / "facts.json"
    with _spawn_tui("approval_surface", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY approval_surface")
        terminal.write_user_text("draft remains")
        _wait_for_stage(facts_path, "approval_open")
        terminal.wait_for_screen_text("Would you like to run")
        terminal.write_user_text("y")
        _wait_for_stage(facts_path, "approval_consumed")

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    details = _details(facts)
    assert details["approval_decision"] == "accept"
    assert details["draft_after_approval"] == "draft remains"


def test_bracketed_paste_preserves_multiline_unicode(tmp_path: Path) -> None:
    """验证真实 Bracketed Paste 不把多行、宽字符和组合字符拆成快捷键。"""
    facts_path = tmp_path / "facts.json"
    pasted = "first\r\nsecond宽🙂e\u0301\n" + ("x" * 8192)
    expected = pasted.replace("\r\n", "\n").strip()
    with _spawn_tui("bracketed_paste", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY bracketed_paste")
        terminal.write_user(
            b"\x1b[200~" + pasted.encode("utf-8") + b"\x1b[201~"
        )
        terminal.wait_for_screen_text("[Pasted Content")
        terminal.send_key(PtyKey.ENTER)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    submissions = _submissions(facts)
    assert len(submissions) == 1
    assert submissions[0]["value"] == expected
    assert submissions[0]["paste_values"] == [pasted.replace("\r\n", "\n")]


def test_focus_events_adjacent_to_input_do_not_consume_keys(tmp_path: Path) -> None:
    """验证 focus gained/lost 与输入相邻时用户文本仍只交付一次。"""
    facts_path = tmp_path / "facts.json"
    value = "focus-safe-🙂"
    with _spawn_tui("focus_adjacent", facts_path) as terminal:
        terminal.wait_for_screen_text("PTY TUI READY focus_adjacent")
        terminal.write_user(b"\x1b[I" + value.encode("utf-8") + b"\x1b[O")
        terminal.wait_for_screen_text("focus-safe-")
        terminal.send_key(PtyKey.ENTER)

        assert terminal.wait_for_exit(timeout=10.0) == 0
        facts = _read_facts(facts_path)

    assert [item["value"] for item in _submissions(facts)] == [value]
