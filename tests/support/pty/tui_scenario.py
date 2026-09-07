import argparse
import asyncio
import json
import sys
import time
import typing
from dataclasses import dataclass
from pathlib import Path

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.protocol.json_value import ThawedJsonValue
from agent.protocol.json_value import freeze_json
from agent.protocol.json_value import thaw_object
from frontends.interaction.contracts import PromptContext
from frontends.tui.adapters.input import create_tui_input
from frontends.tui.contracts.text import FragmentBlock
from frontends.tui.core.interrupt import InterruptDisposition
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.submission import TuiInterruptRequested
from frontends.tui.session.turn_input import TuiTurnInputControl
from infrastructure.skills import SkillSpec
from observability import reset_sinks
from protocol.schema.stream_events import MarkerEvent
from protocol.schema.stream_events import TurnCompletedEvent
from protocol.schema.stream_events import TurnInputAcceptedEvent
from tests.support.fake_mind_chat_server import FakeMindChatServer


_CID = "cid-pty"
_SID = "sid-pty"
_TURN_ID = "turn-pty"


@dataclass(frozen=True, slots=True)
class SubmissionFact:
    """保存跨进程验收可核对的一次提交事实。"""

    value: str
    editable_text: str
    queue_only: bool
    shell_mode: bool
    client_message_id: str
    paste_values: tuple[str, ...]
    attachments: tuple[dict[str, ThawedJsonValue], ...]
    extras: dict[str, ThawedJsonValue]

    @classmethod
    def from_submission(
        cls,
        submission: TuiSubmission,
        *,
        queue_only: bool,
    ) -> "SubmissionFact":
        """从产品提交对象提取不含密钥的稳定字段。"""
        attachments = tuple(
            thaw_object(
                freeze_json(item, field_name="PTY submission attachment"),
                field_name="PTY submission attachment",
            )
            for item in submission.attachments
        )
        extras = thaw_object(
            freeze_json(submission.extras, field_name="PTY submission extras"),
            field_name="PTY submission extras",
        )
        return cls(
            value=submission.value,
            editable_text=submission.editable_text,
            queue_only=queue_only,
            shell_mode=submission.shell_mode,
            client_message_id=submission.client_message_id,
            paste_values=tuple(sorted(submission.paste_store.values())),
            attachments=attachments,
            extras=extras,
        )

    def to_json(self) -> dict[str, ThawedJsonValue]:
        """转换为可直接持久化的 JSON 对象。"""
        return {
            "value": self.value,
            "editable_text": self.editable_text,
            "queue_only": self.queue_only,
            "shell_mode": self.shell_mode,
            "client_message_id": self.client_message_id,
            "paste_values": list(self.paste_values),
            "attachments": list(self.attachments),
            "extras": dict(self.extras),
        }


class ScenarioFacts:
    """拥有单个真实 TUI 场景的脱敏事实文件。"""

    def __init__(self, path: Path, scenario: str) -> None:
        self.path = path
        self.scenario = scenario
        self.stage = "starting"
        self.submissions: list[SubmissionFact] = []
        self.details: dict[str, ThawedJsonValue] = {}

    def record_submission(
        self,
        submission: TuiSubmission,
        *,
        queue_only: bool,
    ) -> None:
        """按实际交付顺序记录提交。"""
        self.submissions.append(SubmissionFact.from_submission(
            submission,
            queue_only=queue_only,
        ))

    def set_detail(self, name: str, value: ThawedJsonValue) -> None:
        """记录单个具名业务或生命周期事实。"""
        self.details[name] = value

    def write(self) -> None:
        """原子替换当前场景事实，供父进程稳定轮询。"""
        payload: dict[str, ThawedJsonValue] = {
            "scenario": self.scenario,
            "stage": self.stage,
            "submissions": [item.to_json() for item in self.submissions],
            "details": dict(self.details),
        }
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        deadline = time.monotonic() + 1.0
        while True:
            try:
                temporary.replace(self.path)
                return None
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)


class _Attachments:
    """提供 Turn input control 所需的最小附件所有权边界。"""

    def __init__(self, items: tuple[dict[str, str], ...] = ()) -> None:
        self._items = list(items)

    def has_pending_attachments(self) -> bool:
        """返回是否仍有待绑定附件。"""
        return bool(self._items)

    def consume_pending_attachments(self) -> list[dict[str, str]]:
        """一次性交付全部待绑定附件。"""
        items = list(self._items)
        self._items.clear()
        return items

    def pending_attachments_snapshot(self) -> tuple[dict[str, str], ...]:
        """返回当前附件快照。"""
        return tuple(self._items)

    def replace_pending_attachments(
        self,
        items: typing.Iterable[dict[str, str]],
    ) -> None:
        """替换当前附件所有权。"""
        self._items = list(items)


class _TurnState:
    """提供 Turn input control 所需的扩展输入所有权边界。"""

    def __init__(self, extras: dict[str, str] | None = None) -> None:
        self._extras = dict(extras or {})

    def consume_pending_prompt_extras(self) -> dict[str, str]:
        """一次性交付全部扩展输入。"""
        extras = dict(self._extras)
        self._extras.clear()
        return extras

    def replace_pending_prompt_extras(self, extras: dict[str, str]) -> None:
        """替换当前扩展输入所有权。"""
        self._extras = dict(extras)


@dataclass(slots=True)
class _Controller:
    """组合测试场景中的附件端口。"""

    attach: _Attachments


@dataclass(slots=True)
class _InterruptCounter:
    """保存真实按键触发的中断处理次数。"""

    value: int = 0


async def _wait_until(
    predicate: typing.Callable[[], bool],
    description: str,
    *,
    timeout: float = 10.0,
) -> None:
    """在共享截止时间内等待稳定业务条件。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError(f"timed out waiting for {description}")
        await asyncio.sleep(0.01)


def _ready(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """同时发布可见就绪标记和跨进程事实。"""
    runtime.append_block(
        FragmentBlock((("", f"PTY TUI READY {facts.scenario}"),)),
        kind="notice",
    )
    facts.stage = "ready"
    facts.write()


def _active_turn(
    runtime: TuiRuntime,
    *,
    attachments: tuple[dict[str, str], ...] = (),
    extras: dict[str, str] | None = None,
    started: bool = True,
) -> tuple[TuiTurnInputControl, FakeMindChatServer]:
    """创建使用真实输入控制器和有状态协议端口的活动 Turn。"""
    server = FakeMindChatServer()
    server.cid = _CID
    server.sid = _SID
    server.turn_id = _TURN_ID
    server.status = "running"
    control = TuiTurnInputControl(
        _Controller(_Attachments(attachments)),
        runtime,
        _TurnState(extras),
        cid=_CID,
        sid=_SID,
        turn_id=_TURN_ID,
        protocol_client=server,
    )
    if started:
        control.handle_event(MarkerEvent(
            type="turn.started",
            turn_id=_TURN_ID,
        ))
    return control, server


def _complete_turn(
    control: TuiTurnInputControl,
    server: FakeMindChatServer,
    *,
    status: str = "completed",
) -> None:
    """把测试 Turn 推进到权威终态。"""
    completed_status = "interrupted" if status == "interrupted" else "completed"
    server.settle(completed_status, last_event_seq=2)
    control.handle_event(TurnCompletedEvent(
        type="turn.completed",
        turn_id=_TURN_ID,
        event_seq=2,
        status=completed_status,
        last_event_seq=2,
        completed_at=1.0,
    ))


async def _close_turn(
    runtime: TuiRuntime,
    control: TuiTurnInputControl,
) -> None:
    """解除活动输入绑定并关闭 Turn control。"""
    runtime.bind_interrupt_handler(None)
    runtime.bind_turn_input_handler(None)
    runtime.bind_queued_restore_handler(None)
    await control.close()
    runtime.set_execution_active(False)


async def _run_idle(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """读取一次空闲提交并保存产品生成的结构化载荷。"""
    _ready(runtime, facts)
    reader = asyncio.create_task(
        runtime.read_message(PromptContext(model="test-model"))
    )
    if facts.scenario == "completion_tab":
        await _wait_until(
            lambda: runtime.screen.input.buffer.text == "/fork",
            "slash completion application",
        )
        facts.stage = "completion_applied"
        facts.write()
    elif facts.scenario == "completion_skill":
        await _wait_until(
            lambda: runtime.screen.input.buffer.text == "$browser ",
            "skill completion application",
        )
        facts.stage = "completion_applied"
        facts.write()
    elif facts.scenario == "completion_file":
        await _wait_until(
            lambda: runtime.screen.input.buffer.text == (
                f"{Path('tests/support/pty/tui_scenario.py')} "
            ),
            "file completion application",
        )
        facts.stage = "completion_applied"
        facts.write()
    await reader
    submission = runtime.consume_submission_payload()
    if submission is None:
        raise RuntimeError("idle TUI submission payload is missing")
    facts.record_submission(submission, queue_only=False)


async def _run_active_inputs(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证活动 Turn 的 Enter steer、Tab queue 和 Shell queue。"""
    control, server = _active_turn(
        runtime,
        attachments=({"kind": "image", "name": "screen.png"},),
        extras={"source": "selection"},
    )
    delivered = asyncio.Event()

    def submit(submission: TuiSubmission, queue_only: bool) -> bool:
        """记录真实按键意图并交给产品 Turn control。"""
        facts.record_submission(submission, queue_only=queue_only)
        handled = control.submit(submission, queue_only)
        if len(facts.submissions) == 3:
            delivered.set()
        return handled

    runtime.bind_turn_input_handler(submit)
    runtime.bind_queued_restore_handler(control.restore_draft)
    _ready(runtime, facts)
    runtime.set_execution_active(True)
    facts.stage = "accepting"
    facts.write()
    await asyncio.wait_for(delivered.wait(), timeout=10.0)
    await _wait_until(
        lambda: len(server.steer_requests) == 1,
        "single steer request",
    )
    first = facts.submissions[0]
    control.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id=_TURN_ID,
        client_message_id=first.client_message_id,
    ))
    facts.set_detail("steer_request_count", len(server.steer_requests))
    facts.set_detail("interrupt_request_count", len(server.interrupt_requests))
    facts.set_detail("queued_active", runtime.submissions.queued_messages.active)
    facts.set_detail("pending_active", runtime.submissions.pending_steers.active)
    _complete_turn(control, server)
    await _close_turn(runtime, control)


async def _run_non_steer_enter(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证中断阶段 Enter 转为下一轮队列。"""
    control, server = _active_turn(
        runtime,
        attachments=({"kind": "image", "name": "queued.png"},),
        extras={"source": "selection"},
    )
    delivered = asyncio.Event()

    def submit(submission: TuiSubmission, queue_only: bool) -> bool:
        """记录不可 steer 阶段的真实 Enter 意图。"""
        handled = control.submit(submission, queue_only)
        captured = runtime.submissions.queued_messages.pop_last()
        if captured is None:
            raise RuntimeError("non-steer submission was not queued")
        facts.record_submission(captured, queue_only=queue_only)
        runtime.submissions.queued_messages.append(captured)
        delivered.set()
        return handled

    runtime.bind_turn_input_handler(submit)
    runtime.bind_queued_restore_handler(control.restore_draft)
    control.request_interrupt()
    await _wait_until(
        lambda: len(server.interrupt_requests) == 1,
        "interrupt request before non-steer input",
    )
    _ready(runtime, facts)
    runtime.set_execution_active(True)
    facts.stage = "accepting"
    facts.write()
    await asyncio.wait_for(delivered.wait(), timeout=10.0)
    facts.set_detail("steer_request_count", len(server.steer_requests))
    facts.set_detail("interrupt_request_count", len(server.interrupt_requests))
    facts.set_detail("queued_active", runtime.submissions.queued_messages.active)
    facts.stage = "queued"
    facts.write()
    acknowledgment = facts.path.with_suffix(".ack")
    await _wait_until(
        acknowledgment.exists,
        "parent screen assertion acknowledgment",
    )
    _complete_turn(control, server, status="interrupted")
    await _close_turn(runtime, control)


async def _run_ctrl_c_draft(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证活动 Turn 的首次 Ctrl-C 仅清除已有草稿。"""
    counter = _InterruptCounter()
    saw_draft = False

    def interrupt() -> InterruptDisposition:
        """记录是否错误触发了 Turn 中断。"""
        counter.value += 1
        return InterruptDisposition.CONSUMED

    runtime.bind_interrupt_handler(interrupt)
    _ready(runtime, facts)
    runtime.set_execution_active(True)
    facts.stage = "accepting"
    facts.write()
    while True:
        text = runtime.screen.input.buffer.text
        if text:
            saw_draft = True
        if saw_draft and not text:
            break
        await asyncio.sleep(0.01)
    facts.set_detail("draft_cleared", True)
    facts.set_detail("interrupt_invocations", counter.value)
    runtime.bind_interrupt_handler(None)
    runtime.set_execution_active(False)


async def _run_ctrl_c_exit(
    runtime: TuiRuntime,
    facts: ScenarioFacts,
    *,
    phase: str,
) -> None:
    """验证不同 Turn 阶段的 Ctrl-C 幂等与二次退出。"""
    control, server = _active_turn(runtime, started=phase != "early")
    if phase == "late":
        _complete_turn(control, server)
    elif phase == "stream_closed":
        control.handle_stream_end("fatal")
    counter = _InterruptCounter()

    def interrupt() -> InterruptDisposition:
        """调用产品幂等中断控制并保留 TUI 消费语义。"""
        counter.value += 1
        control.request_interrupt()
        return InterruptDisposition.CONSUMED

    runtime.bind_interrupt_handler(interrupt)
    _ready(runtime, facts)
    runtime.set_execution_active(True)
    facts.stage = "accepting"
    facts.write()
    reader = asyncio.create_task(
        runtime.read_message(PromptContext(model="test-model"))
    )
    await _wait_until(
        lambda: runtime.submissions.interrupt_state.exit_armed,
        "first Ctrl-C confirmation",
    )
    if phase == "early":
        await _wait_until(
            lambda: len(server.interrupt_requests) == 1,
            "pre-start interrupt request",
        )
        facts.set_detail(
            "request_attempts_before_turn_started",
            len(server.interrupt_requests),
        )
        control.handle_event(MarkerEvent(
            type="turn.started",
            turn_id=_TURN_ID,
        ))
    if phase != "late":
        await _wait_until(
            lambda: len(server.interrupt_requests) == 1,
            "single remote interrupt request",
        )
    facts.stage = "interrupted_once"
    facts.write()
    try:
        await asyncio.wait_for(reader, timeout=10.0)
        raise RuntimeError("second Ctrl-C did not exit TUI input")
    except TuiInterruptRequested:
        facts.set_detail("exit_kind", "interrupt")
    facts.set_detail("interrupt_invocations", counter.value)
    facts.set_detail("interrupt_request_count", len(server.interrupt_requests))
    if phase != "late":
        _complete_turn(control, server, status="interrupted")
    await _close_turn(runtime, control)


async def _run_ctrl_c_expiry(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证退出确认过期后下一次 Ctrl-C 恢复为首次语义。"""
    control, server = _active_turn(runtime)
    counter = _InterruptCounter()

    def interrupt() -> InterruptDisposition:
        """记录确认窗口两侧的幂等中断调用。"""
        counter.value += 1
        control.request_interrupt()
        return InterruptDisposition.CONSUMED

    runtime.bind_interrupt_handler(interrupt)
    _ready(runtime, facts)
    runtime.set_execution_active(True)
    facts.stage = "accepting"
    facts.write()
    reader = asyncio.create_task(
        runtime.read_message(PromptContext(model="test-model"))
    )
    await _wait_until(
        lambda: counter.value == 1,
        "first Ctrl-C",
    )
    await _wait_until(
        lambda: not runtime.submissions.interrupt_state.exit_armed,
        "Ctrl-C confirmation expiry",
        timeout=5.0,
    )
    facts.stage = "expired"
    facts.write()
    await _wait_until(
        lambda: (
            counter.value == 2
            and runtime.submissions.interrupt_state.exit_armed
        ),
        "Ctrl-C confirmation rearm",
    )
    facts.stage = "rearmed"
    facts.write()
    try:
        await asyncio.wait_for(reader, timeout=10.0)
        raise RuntimeError("rearmed Ctrl-C confirmation did not exit")
    except TuiInterruptRequested:
        facts.set_detail("exit_kind", "interrupt")
    facts.set_detail("interrupt_invocations", counter.value)
    facts.set_detail("interrupt_request_count", len(server.interrupt_requests))
    _complete_turn(control, server, status="interrupted")
    await _close_turn(runtime, control)


async def _run_queue_edit(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证最近一条 Tab 队列消息只恢复和重交付一次。"""
    control, server = _active_turn(runtime)
    delivered = asyncio.Event()

    def submit(submission: TuiSubmission, queue_only: bool) -> bool:
        """记录排队和编辑后重交付的两次输入。"""
        facts.record_submission(submission, queue_only=queue_only)
        handled = control.submit(submission, queue_only)
        if len(facts.submissions) == 2:
            delivered.set()
        return handled

    runtime.bind_turn_input_handler(submit)
    runtime.bind_queued_restore_handler(control.restore_draft)
    _ready(runtime, facts)
    runtime.set_execution_active(True)
    facts.stage = "accepting"
    facts.write()
    await _wait_until(
        lambda: len(facts.submissions) == 1,
        "queued input",
    )
    facts.stage = "queued"
    facts.write()
    await _wait_until(
        lambda: (
            runtime.screen.input.buffer.text == "edit once"
            and not runtime.submissions.queued_messages.active
        ),
        "queued input restoration",
    )
    facts.stage = "restored"
    facts.write()
    await asyncio.wait_for(delivered.wait(), timeout=10.0)
    await _wait_until(
        lambda: len(server.steer_requests) == 1,
        "edited steer request",
    )
    control.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id=_TURN_ID,
        client_message_id=facts.submissions[-1].client_message_id,
    ))
    facts.set_detail("queued_active", runtime.submissions.queued_messages.active)
    facts.set_detail("steer_request_count", len(server.steer_requests))
    _complete_turn(control, server)
    await _close_turn(runtime, control)


def _submission(value: str) -> TuiSubmission:
    """创建带稳定身份的恢复顺序测试提交。"""
    return TuiSubmission(
        value=value,
        editable_text=value,
        paste_store={},
        client_message_id=f"message-{value.replace(' ', '-')}",
    )


async def _run_interrupt_restore_order(
    runtime: TuiRuntime,
    facts: ScenarioFacts,
) -> None:
    """验证真实中断后的四层输入按稳定优先级恢复。"""
    control, server = _active_turn(runtime)

    def interrupt() -> InterruptDisposition:
        """把真实 Ctrl-C 交给活动 Turn 的中断控制器。"""
        control.request_interrupt()
        return InterruptDisposition.CONSUMED

    runtime.track_pending_steer(_submission("pending steer"))
    runtime.defer_rejected_steer(_submission("rejected steer"))
    runtime.defer_submission(_submission("tab follow up"))
    runtime.bind_interrupt_handler(interrupt)
    _ready(runtime, facts)
    runtime.set_execution_active(True)
    facts.stage = "accepting"
    facts.write()
    await _wait_until(
        lambda: runtime.submissions.interrupt_state.exit_armed,
        "restore-order interrupt",
    )
    facts.stage = "interrupted"
    facts.write()
    await _wait_until(
        lambda: runtime.screen.input.buffer.text == "current draft",
        "draft typed during interruption",
    )
    runtime.begin_interrupt_settlement()
    runtime.finish_interrupted_presentation()
    if not runtime.restore_interrupted_submissions():
        raise RuntimeError("interrupted submissions were not restored")
    restored = runtime.screen.input.buffer.text
    facts.set_detail("restored_text", restored)
    facts.set_detail("pending_active", runtime.submissions.pending_steers.active)
    facts.set_detail("rejected_active", runtime.submissions.rejected_steers.active)
    facts.set_detail("queued_active", runtime.submissions.queued_messages.active)
    facts.stage = "restored"
    facts.write()
    acknowledgment = facts.path.with_suffix(".ack")
    await _wait_until(acknowledgment.exists, "restored screen acknowledgment")
    _complete_turn(control, server, status="interrupted")
    await _close_turn(runtime, control)


async def _run_nested_surfaces(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证补全与记录页依次拥有其活动按键。"""
    runtime.append_block(
        FragmentBlock((("", "needle in committed transcript"),)),
        kind="assistant",
    )
    _ready(runtime, facts)
    reader = asyncio.create_task(
        runtime.read_message(PromptContext(model="test-model"))
    )
    await _wait_until(
        lambda: (
            runtime.screen.input.buffer.text == "/f"
            and runtime.screen.input.buffer.complete_state is not None
        ),
        "slash completion surface",
    )
    facts.stage = "completion_open"
    facts.write()
    await _wait_until(
        lambda: runtime.screen.input.buffer.complete_state is None,
        "slash completion close",
    )
    facts.set_detail("draft_after_completion", runtime.screen.input.buffer.text)
    facts.stage = "completion_closed"
    facts.write()
    await _wait_until(
        lambda: runtime.screen.transcript_overlay.active,
        "transcript overlay",
    )
    facts.stage = "transcript_open"
    facts.write()
    await _wait_until(
        lambda: runtime.screen.transcript_overlay.search_query == "needle",
        "transcript search input",
    )
    facts.set_detail("draft_during_transcript", runtime.screen.input.buffer.text)
    facts.stage = "transcript_search"
    facts.write()
    await _wait_until(
        lambda: not runtime.screen.transcript_overlay.search_editing,
        "transcript search close",
    )
    facts.stage = "transcript_search_closed"
    facts.write()
    await _wait_until(
        lambda: not runtime.screen.transcript_overlay.active,
        "transcript overlay close",
    )
    facts.set_detail("draft_after_transcript", runtime.screen.input.buffer.text)
    facts.stage = "surfaces_consumed"
    facts.write()
    acknowledgment = facts.path.with_suffix(".ack")
    await _wait_until(
        acknowledgment.exists,
        "nested surfaces assertion acknowledgment",
    )
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


async def _run_approval_surface(runtime: TuiRuntime, facts: ScenarioFacts) -> None:
    """验证审批表面消费按键且不改写主输入草稿。"""
    _ready(runtime, facts)
    reader = asyncio.create_task(
        runtime.read_message(PromptContext(model="test-model"))
    )
    await _wait_until(
        lambda: runtime.screen.input.buffer.text == "draft remains",
        "approval background draft",
    )
    render_counter = runtime.screen.application.render_counter
    approval = asyncio.create_task(ApprovalCoordinator(runtime).request({
        "tool": "shell_command",
        "command": "echo nested-surface",
        "show_timer": False,
    }))
    await _wait_until(
        lambda: (
            runtime.screen.approval.state is not None
            and runtime.screen.application.layout.current_control
            is runtime.screen.approval_control
            and runtime.screen.application.render_counter > render_counter
        ),
        "rendered approval focus",
    )
    facts.stage = "approval_open"
    facts.write()
    decision = await approval
    facts.set_detail("approval_decision", decision)
    facts.set_detail("draft_after_approval", runtime.screen.input.buffer.text)
    facts.stage = "approval_consumed"
    facts.write()
    acknowledgment = facts.path.with_suffix(".ack")
    await _wait_until(
        acknowledgment.exists,
        "approval assertion acknowledgment",
    )
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


async def _run(scenario: str, facts_path: Path) -> None:
    """在当前原生终端中运行指定产品 TUI 场景。"""
    reset_sinks()
    facts = ScenarioFacts(facts_path, scenario)
    runtime = TuiRuntime(input_obj=create_tui_input(sys.stdin))
    if scenario == "completion_skill":
        runtime.input_model.set_skills((SkillSpec(
            name="browser",
            description="Inspect a browser session",
            source="test",
            root=Path.cwd(),
            entry=Path.cwd() / "SKILL.md",
        ),))
    elif scenario == "completion_file":
        runtime.input_model.set_workspace_root(Path.cwd())
    await runtime.open()
    try:
        if scenario in {
            "idle_enter",
            "idle_tab",
            "completion_tab",
            "completion_skill",
            "completion_file",
            "bracketed_paste",
            "focus_adjacent",
        }:
            await _run_idle(runtime, facts)
        elif scenario == "active_inputs":
            await _run_active_inputs(runtime, facts)
        elif scenario == "non_steer_enter":
            await _run_non_steer_enter(runtime, facts)
        elif scenario == "ctrl_c_draft":
            await _run_ctrl_c_draft(runtime, facts)
        elif scenario in {"ctrl_c_early", "ctrl_c_late", "ctrl_c_stream_closed"}:
            await _run_ctrl_c_exit(
                runtime,
                facts,
                phase=scenario.removeprefix("ctrl_c_"),
            )
        elif scenario == "ctrl_c_expiry":
            await _run_ctrl_c_expiry(runtime, facts)
        elif scenario == "queue_edit":
            await _run_queue_edit(runtime, facts)
        elif scenario == "interrupt_restore_order":
            await _run_interrupt_restore_order(runtime, facts)
        elif scenario == "nested_surfaces":
            await _run_nested_surfaces(runtime, facts)
        elif scenario == "approval_surface":
            await _run_approval_surface(runtime, facts)
        else:
            raise ValueError(f"unsupported PTY TUI scenario: {scenario}")
        facts.stage = "complete"
    finally:
        await runtime.close()
        facts.write()


def main() -> int:
    """解析测试入口参数并运行真实 TUI 场景。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("facts", type=Path)
    arguments = parser.parse_args()
    asyncio.run(_run(arguments.scenario, arguments.facts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
