"""提供 Turn 生命周期的状态模型、组合场景和统一不变量断言。"""

import enum
import itertools
import random
from dataclasses import dataclass

from tests.scenarios.frames import (
    FrameIndicator,
    FrameKind,
    FrameTrace,
    LogicalFrame,
    RuntimeInvariantError,
)


class TurnPhase(enum.Enum):
    BEFORE_START = "before_start"
    MODEL_WAIT = "model_wait"
    ASSISTANT_STREAMING = "assistant_streaming"
    TOOL = "tool"
    APPROVAL = "approval"
    RETRY = "retry"
    REPLAY = "replay"
    COMPLETED = "completed"


class UserAction(enum.Enum):
    NONE = "none"
    STEER = "steer"
    MULTIPLE_STEERS = "multiple_steers"
    TAB_QUEUE = "tab_queue"
    INTERRUPT = "interrupt"
    INTERRUPT_AND_NEW_INPUT = "interrupt_and_new_input"


class TransportCondition(enum.Enum):
    NORMAL = "normal"
    RESPONSE_LOST = "response_lost"
    TIMEOUT = "timeout"
    RECONNECT = "reconnect"
    DUPLICATE_EVENT = "duplicate_event"
    GAP = "gap"
    LATE_EVENT = "late_event"


class ServerResult(enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class InterruptInputCase(enum.Enum):
    ZERO = "zero"
    ONE_STEER = "one_steer"
    MANY_STEERS = "many_steers"
    NEXT_TURN_INPUT = "next_turn_input"


class InterruptPhase(enum.Enum):
    BEFORE_START = "before_start"
    THINKING = "thinking"
    ASSISTANT_STREAMING = "assistant_streaming"
    TOOL = "tool"
    APPROVAL = "approval"
    PROVIDER_RETRY = "provider_retry"
    TRANSPORT_RETRY = "transport_retry"
    REPLAY = "replay"


class InterruptTransport(enum.Enum):
    SUCCESS = "success"
    RESPONSE_LOST = "response_lost"
    TIMEOUT = "timeout"
    RETRYABLE_ERROR = "retryable_error"


class InputOwner(enum.Enum):
    SENT_STEER = "sent_steer"
    COMMITTED_CURRENT = "committed_current"
    QUEUED_NEXT = "queued_next"
    UNCERTAIN = "uncertain"
    CONSUMED_NEXT = "consumed_next"


class RuntimeLease(enum.Enum):
    TURN_TASK = "turn_task"
    TIMER = "timer"
    TOOL = "tool"
    APPROVAL = "approval"
    RETRY = "retry"
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True)
class TurnScenario:
    """描述一个可组合的 Turn 生命周期场景。"""

    phase: TurnPhase
    action: UserAction
    transport: TransportCondition
    server_result: ServerResult

    @property
    def identifier(self) -> str:
        """返回适合 pytest 报告的稳定场景标识。"""
        return "-".join((
            self.phase.value,
            self.action.value,
            self.transport.value,
            self.server_result.value,
        ))


@dataclass(frozen=True, slots=True)
class InterruptScenario:
    """描述中断矩阵中的一个确定组合。"""

    phase: InterruptPhase
    inputs: InterruptInputCase
    transport: InterruptTransport

    @property
    def identifier(self) -> str:
        """返回适合 pytest 报告的稳定场景标识。"""
        return "-".join((
            self.phase.value,
            self.inputs.value,
            self.transport.value,
        ))


@dataclass(frozen=True, slots=True)
class InputRecord:
    """记录一条用户输入当前唯一的生命周期归属。"""

    client_message_id: str
    ordinal: int
    owner: InputOwner
    turn_id: str


class TurnScenarioHarness:
    """维护独立 Turn oracle，并在每个转换后验证八条不变量。"""

    def __init__(self) -> None:
        self.turn_id = ""
        self.phase = TurnPhase.COMPLETED
        self.epoch = 0
        self.terminal = True
        self.interrupt_accepted = False
        self.execution_gate_open = True
        self.cursor = 0
        self.next_turn_cursor = 0
        self.last_event_seq = 0
        self.terminal_snapshot_seq: int | None = None
        self.inputs: dict[str, InputRecord] = {}
        self.next_queue: list[str] = []
        self.consumed_next: list[str] = []
        self.leases: set[RuntimeLease] = set()
        self.frames = FrameTrace()
        self.trace: list[str] = []
        self._next_ordinal = 0
        self._cursor_history: list[int] = [0]
        self._stale_state_mutations = 0

    def start_turn(self, turn_id: str, phase: TurnPhase) -> None:
        """在执行门禁开放时启动一个具有新 epoch 的 Turn。"""
        if not self.execution_gate_open:
            self._fail("Execution Gate", "next turn started before terminal")
        self.turn_id = turn_id
        self.phase = phase
        self.epoch += 1
        self.terminal = False
        self.interrupt_accepted = False
        self.execution_gate_open = False
        self.next_turn_cursor = self.cursor
        self.terminal_snapshot_seq = None
        self.leases = {RuntimeLease.TURN_TASK}
        self._record(f"start({turn_id}, phase={phase.value}, cursor={self.cursor})")
        self.assert_invariants()

    def set_phase(self, phase: TurnPhase) -> None:
        """切换当前 Turn 的执行阶段并同步阶段资源。"""
        if self.terminal:
            return None
        self.phase = phase
        self.leases.difference_update({
            RuntimeLease.TIMER,
            RuntimeLease.TOOL,
            RuntimeLease.APPROVAL,
            RuntimeLease.RETRY,
        })
        if phase in {TurnPhase.MODEL_WAIT, TurnPhase.BEFORE_START}:
            self.leases.add(RuntimeLease.TIMER)
        elif phase is TurnPhase.TOOL:
            self.leases.add(RuntimeLease.TOOL)
        elif phase is TurnPhase.APPROVAL:
            self.leases.update({RuntimeLease.TOOL, RuntimeLease.APPROVAL})
        elif phase is TurnPhase.RETRY:
            self.leases.add(RuntimeLease.RETRY)
        elif phase is TurnPhase.REPLAY:
            self.leases.add(RuntimeLease.RECONCILE)
        self._record(f"phase({phase.value})")
        self.assert_invariants()

    def submit_input(self, client_message_id: str, *, queue_only: bool = False) -> None:
        """按当前门禁把一条新输入归入 steer 或下一轮队列。"""
        if client_message_id in self.inputs:
            self._record(f"duplicate_input({client_message_id})")
            self.assert_invariants()
            return None
        self._next_ordinal += 1
        queued = queue_only or self.interrupt_accepted or self.terminal
        owner = InputOwner.QUEUED_NEXT if queued else InputOwner.SENT_STEER
        self.inputs[client_message_id] = InputRecord(
            client_message_id,
            self._next_ordinal,
            owner,
            self.turn_id,
        )
        if queued:
            self.next_queue.append(client_message_id)
        self._record(f"submit({client_message_id}, owner={owner.value})")
        self.assert_invariants()

    def reconcile_input(self, client_message_id: str, owner: InputOwner) -> None:
        """依据服务端权威结果迁移一条未确认输入。"""
        if owner not in {
            InputOwner.COMMITTED_CURRENT,
            InputOwner.QUEUED_NEXT,
            InputOwner.UNCERTAIN,
        }:
            raise ValueError("reconciliation requires a stable input owner")
        record = self.inputs[client_message_id]
        if record.owner is InputOwner.QUEUED_NEXT:
            self.next_queue.remove(client_message_id)
        self.inputs[client_message_id] = InputRecord(
            record.client_message_id,
            record.ordinal,
            owner,
            record.turn_id,
        )
        if owner is InputOwner.QUEUED_NEXT:
            self.next_queue.append(client_message_id)
            self.next_queue.sort(key=lambda item: self.inputs[item].ordinal)
        self._record(f"reconcile({client_message_id}, owner={owner.value})")
        self.assert_invariants()

    def accept_interrupt(self, transport: InterruptTransport) -> None:
        """登记中断事实，但不把 HTTP 回执解释为 Turn 终态。"""
        if self.terminal:
            return None
        self.interrupt_accepted = True
        self.leases.add(RuntimeLease.RECONCILE)
        self._record(f"interrupt(transport={transport.value})")
        self.assert_invariants()

    def observe_event(
        self,
        *,
        turn_id: str,
        epoch: int,
        event_seq: int,
        duplicate: bool = False,
    ) -> None:
        """归约具有稳定身份的事件，并忽略旧 Turn、旧 epoch 和重复事件。"""
        before = (
            self.phase,
            self.cursor,
            self.last_event_seq,
            frozenset(self.leases),
        )
        if turn_id != self.turn_id or epoch != self.epoch:
            self._record(
                f"ignore_stale(turn={turn_id}, epoch={epoch}, seq={event_seq})"
            )
            after = (
                self.phase,
                self.cursor,
                self.last_event_seq,
                frozenset(self.leases),
            )
            if after != before:
                self._stale_state_mutations += 1
            self.assert_invariants()
            return None
        if duplicate or event_seq <= self.last_event_seq:
            self._record(f"ignore_duplicate(seq={event_seq})")
            self.assert_invariants()
            return None
        if self.last_event_seq and event_seq > self.last_event_seq + 1:
            self.leases.add(RuntimeLease.RECONCILE)
            self._record(
                f"gap(expected={self.last_event_seq + 1}, actual={event_seq})"
            )
        self.last_event_seq = event_seq
        self._record(f"event(seq={event_seq})")
        self.assert_invariants()

    def observe_status(
        self,
        result: ServerResult,
        *,
        last_event_seq: int,
    ) -> None:
        """应用服务端状态快照，并只在权威终态开放下一 Turn。"""
        terminal = result in {
            ServerResult.COMPLETED,
            ServerResult.FAILED,
            ServerResult.INTERRUPTED,
            ServerResult.CANCELLED,
        }
        self._record(
            f"status({result.value}, terminal={terminal}, seq={last_event_seq})"
        )
        if not terminal:
            self.assert_invariants()
            return None
        self.terminal = True
        self.phase = TurnPhase.COMPLETED
        self.execution_gate_open = True
        self.terminal_snapshot_seq = last_event_seq
        self.cursor = max(self.cursor, last_event_seq)
        self._cursor_history.append(self.cursor)
        self.leases.clear()
        self.assert_invariants()

    def consume_next_input(self) -> str | None:
        """按 FIFO 取出下一 Turn 的首条输入。"""
        if not self.next_queue:
            return None
        client_message_id = self.next_queue.pop(0)
        record = self.inputs[client_message_id]
        self.inputs[client_message_id] = InputRecord(
            record.client_message_id,
            record.ordinal,
            InputOwner.CONSUMED_NEXT,
            record.turn_id,
        )
        self.consumed_next.append(client_message_id)
        self._record(f"consume_next({client_message_id})")
        self.assert_invariants()
        return client_message_id

    def append_frame(self, frame: LogicalFrame) -> None:
        """记录当前 Turn 的一帧；旧身份画面只记入事件 trace。"""
        if frame.turn_id != self.turn_id:
            self._record(f"ignore_stale_frame(turn={frame.turn_id})")
            self.assert_invariants()
            return None
        self.frames.append(frame)
        self._record(
            f"frame({frame.kind.value}, indicator={frame.indicator.value})"
        )
        self.assert_invariants()

    def assert_invariants(self) -> None:
        """统一验证八条 Agent Runtime 核心不变量。"""
        if self.interrupt_accepted and not self.terminal:
            if self.execution_gate_open:
                self._fail(
                    "Turn Authority",
                    "interrupt receipt opened the execution gate",
                )
        if not self.terminal and self.execution_gate_open:
            self._fail("Execution Gate", "active turn left its gate open")

        queued_ids = {
            client_message_id
            for client_message_id, record in self.inputs.items()
            if record.owner is InputOwner.QUEUED_NEXT
        }
        if queued_ids != set(self.next_queue):
            self._fail("Input Exactly-One", "queue and owner ledger diverged")
        if len(self.next_queue) != len(set(self.next_queue)):
            self._fail("Input Exactly-One", "message exists twice in next queue")

        queue_ordinals = [self.inputs[item].ordinal for item in self.next_queue]
        if queue_ordinals != sorted(queue_ordinals):
            self._fail("FIFO", "next-turn queue order changed")
        consumed_ordinals = [
            self.inputs[item].ordinal for item in self.consumed_next
        ]
        if consumed_ordinals != sorted(consumed_ordinals):
            self._fail("FIFO", "consumed next-turn order changed")

        if self._cursor_history != sorted(self._cursor_history):
            self._fail("Cursor", "session cursor moved backwards")
        if (
            self.terminal_snapshot_seq is not None
            and self.cursor < self.terminal_snapshot_seq
        ):
            self._fail("Cursor", "terminal cursor was not committed")
        if not self.terminal and self.next_turn_cursor != self.cursor:
            self._fail("Cursor", "next turn did not start at session cursor")

        if self._stale_state_mutations:
            self._fail("Isolation", "stale event mutated current turn")
        if self.terminal and self.leases:
            self._fail("Terminal Cleanup", "terminal turn retained resources")
        self.frames.assert_contract()

    def trace_text(self) -> str:
        """返回带序号、可直接重放的场景事件列表。"""
        return "\n".join(
            f"{index} {event}"
            for index, event in enumerate(self.trace, start=1)
        )

    def _record(self, event: str) -> None:
        owners = ",".join(
            f"{record.client_message_id}:{record.owner.value}"
            for record in sorted(
                self.inputs.values(),
                key=lambda item: item.ordinal,
            )
        )
        leases = ",".join(sorted(lease.value for lease in self.leases))
        self.trace.append(
            f"{event} | turn={self.turn_id or '-'} epoch={self.epoch} "
            f"event_seq={self.last_event_seq} cursor={self.cursor} "
            f"terminal={self.terminal} gate={self.execution_gate_open} "
            f"owners=[{owners}] leases=[{leases}]"
        )

    def _fail(self, invariant: str, detail: str) -> None:
        trace = self.trace_text()
        raise RuntimeInvariantError(
            f"{invariant}: {detail}\n\n{trace}"
        )


def scenario_pair_keys(scenario: TurnScenario) -> frozenset[str]:
    """返回一个 Scenario 覆盖的全部二维值对。"""
    values = (
        ("phase", scenario.phase.value),
        ("action", scenario.action.value),
        ("transport", scenario.transport.value),
        ("server", scenario.server_result.value),
    )
    return frozenset(
        f"{left_name}={left_value}|{right_name}={right_value}"
        for (left_name, left_value), (right_name, right_value)
        in itertools.combinations(values, 2)
    )


def pairwise_turn_scenarios() -> tuple[TurnScenario, ...]:
    """贪心选择覆盖四个维度全部值对的确定性场景集合。"""
    candidates = list(
        TurnScenario(phase, action, transport, result)
        for phase, action, transport, result in itertools.product(
            TurnPhase,
            UserAction,
            TransportCondition,
            ServerResult,
        )
    )
    uncovered: set[str] = set()
    for candidate in candidates:
        uncovered.update(scenario_pair_keys(candidate))

    selected: list[TurnScenario] = []
    while uncovered:
        best = max(
            candidates,
            key=lambda candidate: len(
                scenario_pair_keys(candidate).intersection(uncovered)
            ),
        )
        covered = scenario_pair_keys(best).intersection(uncovered)
        if not covered:
            raise RuntimeError("pairwise scenario selection did not converge")
        selected.append(best)
        uncovered.difference_update(covered)
        candidates.remove(best)
    return tuple(selected)


def interrupt_scenarios() -> tuple[InterruptScenario, ...]:
    """返回中断阶段、输入数量与命令传输的完整 P0 矩阵。"""
    return tuple(
        InterruptScenario(phase, inputs, transport)
        for phase, inputs, transport in itertools.product(
            InterruptPhase,
            InterruptInputCase,
            InterruptTransport,
        )
    )


def run_turn_scenario(scenario: TurnScenario) -> TurnScenarioHarness:
    """执行一个组合 Scenario 并返回已经验证的最终 oracle。"""
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", scenario.phase)
    harness.set_phase(scenario.phase)

    if scenario.action is UserAction.STEER:
        harness.submit_input("message-1")
    elif scenario.action is UserAction.MULTIPLE_STEERS:
        for client_message_id in ("message-1", "message-2", "message-3"):
            harness.submit_input(client_message_id)
    elif scenario.action is UserAction.TAB_QUEUE:
        harness.submit_input("message-1", queue_only=True)
    elif scenario.action is UserAction.INTERRUPT:
        harness.accept_interrupt(_interrupt_transport(scenario.transport))
    elif scenario.action is UserAction.INTERRUPT_AND_NEW_INPUT:
        harness.accept_interrupt(_interrupt_transport(scenario.transport))
        harness.submit_input("message-after-interrupt")

    if scenario.transport is TransportCondition.RECONNECT:
        harness.set_phase(TurnPhase.RETRY)
    elif scenario.transport is TransportCondition.DUPLICATE_EVENT:
        harness.observe_event(
            turn_id="turn-1",
            epoch=harness.epoch,
            event_seq=1,
        )
        harness.observe_event(
            turn_id="turn-1",
            epoch=harness.epoch,
            event_seq=1,
            duplicate=True,
        )
    elif scenario.transport is TransportCondition.GAP:
        harness.observe_event(
            turn_id="turn-1",
            epoch=harness.epoch,
            event_seq=2,
        )
        harness.observe_event(
            turn_id="turn-1",
            epoch=harness.epoch,
            event_seq=4,
        )
    elif scenario.transport is TransportCondition.LATE_EVENT:
        harness.observe_event(
            turn_id="turn-old",
            epoch=max(0, harness.epoch - 1),
            event_seq=99,
        )

    unresolved = tuple(
        client_message_id
        for client_message_id, record in harness.inputs.items()
        if record.owner is InputOwner.SENT_STEER
    )
    for client_message_id in unresolved:
        if scenario.server_result is ServerResult.RECONCILIATION_REQUIRED:
            owner = InputOwner.UNCERTAIN
        elif scenario.server_result in {
            ServerResult.FAILED,
            ServerResult.INTERRUPTED,
            ServerResult.CANCELLED,
        }:
            owner = InputOwner.QUEUED_NEXT
        else:
            owner = InputOwner.COMMITTED_CURRENT
        harness.reconcile_input(client_message_id, owner)

    harness.observe_status(
        scenario.server_result,
        last_event_seq=max(1, harness.last_event_seq),
    )
    harness.assert_invariants()
    return harness


def run_interrupt_scenario(scenario: InterruptScenario) -> TurnScenarioHarness:
    """执行一个完整中断矩阵组合并验证终态前后的门禁。"""
    harness = TurnScenarioHarness()
    phase = _turn_phase_from_interrupt(scenario.phase)
    harness.start_turn("turn-1", phase)
    harness.set_phase(phase)

    steer_ids: tuple[str, ...] = ()
    if scenario.inputs is InterruptInputCase.ONE_STEER:
        steer_ids = ("message-1",)
    elif scenario.inputs is InterruptInputCase.MANY_STEERS:
        steer_ids = ("message-1", "message-2", "message-3")
    for client_message_id in steer_ids:
        harness.submit_input(client_message_id)

    harness.accept_interrupt(scenario.transport)
    if scenario.inputs is InterruptInputCase.NEXT_TURN_INPUT:
        harness.submit_input("message-next")

    harness.observe_status(ServerResult.RUNNING, last_event_seq=7)
    if harness.execution_gate_open:
        raise RuntimeInvariantError("Execution Gate opened before terminal status")

    for client_message_id in steer_ids:
        harness.reconcile_input(client_message_id, InputOwner.QUEUED_NEXT)
    harness.observe_status(ServerResult.INTERRUPTED, last_event_seq=18)
    while harness.next_queue:
        harness.consume_next_input()
    harness.assert_invariants()
    return harness


def run_seeded_trace(seed: int, steps: int) -> TurnScenarioHarness:
    """生成可重放长序列，并在每个事件后验证不变量。"""
    generator = random.Random(seed)
    harness = TurnScenarioHarness()
    next_turn = 1
    next_message = 1
    next_seq = 1

    try:
        for _ in range(steps):
            if harness.terminal:
                harness.start_turn(
                    f"turn-{next_turn}",
                    generator.choice(tuple(TurnPhase)[:-1]),
                )
                next_turn += 1
                continue

            action = generator.randrange(9)
            if action == 0:
                harness.submit_input(f"message-{next_message}")
                next_message += 1
            elif action == 1:
                harness.submit_input(
                    f"message-{next_message}",
                    queue_only=True,
                )
                next_message += 1
            elif action == 2:
                harness.accept_interrupt(generator.choice(tuple(InterruptTransport)))
            elif action == 3:
                harness.set_phase(generator.choice(tuple(TurnPhase)[:-1]))
            elif action == 4:
                harness.observe_event(
                    turn_id=harness.turn_id,
                    epoch=harness.epoch,
                    event_seq=next_seq,
                )
                next_seq += 1
            elif action == 5:
                harness.observe_event(
                    turn_id="turn-stale",
                    epoch=max(0, harness.epoch - 1),
                    event_seq=next_seq + 50,
                )
            elif action == 6:
                _reconcile_random_input(harness, generator)
            elif action == 7:
                harness.append_frame(_valid_frame(harness, generator))
            else:
                _settle_random_turn(harness, generator, next_seq)
                next_seq += 1
                while harness.next_queue and generator.choice((False, True)):
                    harness.consume_next_input()
            harness.assert_invariants()
    except RuntimeInvariantError as error:
        raise AssertionError(
            f"seed={seed}\n\n{harness.trace_text()}\n\n{error}"
        ) from error
    return harness


def _interrupt_transport(transport: TransportCondition) -> InterruptTransport:
    if transport is TransportCondition.RESPONSE_LOST:
        return InterruptTransport.RESPONSE_LOST
    if transport is TransportCondition.TIMEOUT:
        return InterruptTransport.TIMEOUT
    return InterruptTransport.SUCCESS


def _turn_phase_from_interrupt(phase: InterruptPhase) -> TurnPhase:
    if phase is InterruptPhase.BEFORE_START:
        return TurnPhase.BEFORE_START
    if phase is InterruptPhase.THINKING:
        return TurnPhase.MODEL_WAIT
    if phase is InterruptPhase.ASSISTANT_STREAMING:
        return TurnPhase.ASSISTANT_STREAMING
    if phase is InterruptPhase.TOOL:
        return TurnPhase.TOOL
    if phase is InterruptPhase.APPROVAL:
        return TurnPhase.APPROVAL
    if phase in {
        InterruptPhase.PROVIDER_RETRY,
        InterruptPhase.TRANSPORT_RETRY,
    }:
        return TurnPhase.RETRY
    return TurnPhase.REPLAY


def _reconcile_random_input(
    harness: TurnScenarioHarness,
    generator: random.Random,
) -> None:
    unresolved = tuple(
        client_message_id
        for client_message_id, record in harness.inputs.items()
        if record.owner is InputOwner.SENT_STEER
    )
    if not unresolved:
        return None
    harness.reconcile_input(
        generator.choice(unresolved),
        generator.choice((
            InputOwner.COMMITTED_CURRENT,
            InputOwner.QUEUED_NEXT,
            InputOwner.UNCERTAIN,
        )),
    )


def _settle_random_turn(
    harness: TurnScenarioHarness,
    generator: random.Random,
    event_seq: int,
) -> None:
    unresolved = tuple(
        client_message_id
        for client_message_id, record in harness.inputs.items()
        if record.owner is InputOwner.SENT_STEER
    )
    for client_message_id in unresolved:
        harness.reconcile_input(client_message_id, InputOwner.QUEUED_NEXT)
    harness.observe_status(
        generator.choice((
            ServerResult.COMPLETED,
            ServerResult.FAILED,
            ServerResult.INTERRUPTED,
            ServerResult.CANCELLED,
        )),
        last_event_seq=event_seq,
    )
    harness.append_frame(LogicalFrame(
        turn_id=harness.turn_id,
        indicator=FrameIndicator.HIDDEN,
        kind=FrameKind.TERMINAL,
    ))


def _valid_frame(
    harness: TurnScenarioHarness,
    generator: random.Random,
) -> LogicalFrame:
    if harness.phase is TurnPhase.ASSISTANT_STREAMING:
        return LogicalFrame(
            turn_id=harness.turn_id,
            indicator=FrameIndicator.HIDDEN,
            assistant_text="assistant",
            kind=FrameKind.ASSISTANT_HANDOFF,
        )
    if harness.phase is TurnPhase.APPROVAL:
        if generator.choice((False, True)):
            return LogicalFrame(
                turn_id=harness.turn_id,
                indicator=FrameIndicator.WORKING,
                kind=FrameKind.APPROVAL_COMPLETED,
                tool_leases=1,
            )
        return LogicalFrame(
            turn_id=harness.turn_id,
            indicator=FrameIndicator.APPROVAL,
            tool_leases=1,
            approval_leases=1,
        )
    if harness.phase is TurnPhase.RETRY:
        return LogicalFrame(
            turn_id=harness.turn_id,
            indicator=FrameIndicator.RETRYING,
        )
    return LogicalFrame(
        turn_id=harness.turn_id,
        indicator=FrameIndicator.THINKING,
    )
