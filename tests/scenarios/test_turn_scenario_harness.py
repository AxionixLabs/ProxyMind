"""验证 Turn Scenario Harness 的组合覆盖与核心不变量。"""

import itertools

import pytest

from tests.scenarios.turns import (
    FrameIndicator,
    FrameKind,
    InputOwner,
    InterruptScenario,
    InterruptTransport,
    LogicalFrame,
    RuntimeInvariantError,
    RuntimeLease,
    ServerResult,
    TransportCondition,
    TurnPhase,
    TurnScenario,
    TurnScenarioHarness,
    UserAction,
    interrupt_scenarios,
    pairwise_turn_scenarios,
    run_interrupt_scenario,
    run_seeded_trace,
    run_turn_scenario,
    scenario_pair_keys,
)


PAIRWISE_SCENARIOS = pairwise_turn_scenarios()
INTERRUPT_SCENARIOS = interrupt_scenarios()


@pytest.mark.runtime_p0
def test_pairwise_selector_covers_every_dimension_value_pair() -> None:
    """验证精简场景集没有遗漏完整空间中的任意二维组合。"""
    full_space = (
        TurnScenario(phase, action, transport, result)
        for phase, action, transport, result in itertools.product(
            TurnPhase,
            UserAction,
            TransportCondition,
            ServerResult,
        )
    )
    expected_pairs: set[str] = set()
    for scenario in full_space:
        expected_pairs.update(scenario_pair_keys(scenario))

    selected_pairs: set[str] = set()
    for scenario in PAIRWISE_SCENARIOS:
        selected_pairs.update(scenario_pair_keys(scenario))

    assert selected_pairs == expected_pairs
    assert len(PAIRWISE_SCENARIOS) < (
        len(TurnPhase)
        * len(UserAction)
        * len(TransportCondition)
        * len(ServerResult)
    )


@pytest.mark.runtime_p0
@pytest.mark.parametrize(
    "scenario",
    PAIRWISE_SCENARIOS,
    ids=lambda scenario: scenario.identifier,
)
def test_pairwise_turn_state_space_preserves_all_invariants(
    scenario: TurnScenario,
) -> None:
    """对 pairwise 主状态空间统一执行八条 Runtime 不变量。"""
    harness = run_turn_scenario(scenario)

    harness.assert_invariants()


@pytest.mark.runtime_p0
@pytest.mark.parametrize(
    "scenario",
    INTERRUPT_SCENARIOS,
    ids=lambda scenario: scenario.identifier,
)
def test_complete_interrupt_matrix_preserves_gate_input_and_fifo(
    scenario: InterruptScenario,
) -> None:
    """覆盖八种时机、四种输入形态和四种传输结果。"""
    harness = run_interrupt_scenario(scenario)

    assert harness.terminal
    assert harness.execution_gate_open
    assert harness.cursor == 18
    harness.assert_invariants()


@pytest.mark.runtime_stateful
@pytest.mark.parametrize("seed", range(10))
def test_seeded_long_traces_preserve_runtime_invariants(seed: int) -> None:
    """累计执行一万步可重放随机事件序列。"""
    harness = run_seeded_trace(seed=seed, steps=1000)

    harness.assert_invariants()


def test_turn_authority_assertion_detects_interrupt_as_terminal() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.MODEL_WAIT)
    harness.accept_interrupt(InterruptTransport.SUCCESS)
    harness.execution_gate_open = True

    with pytest.raises(RuntimeInvariantError, match="Turn Authority"):
        harness.assert_invariants()


def test_execution_gate_assertion_detects_early_next_turn() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.MODEL_WAIT)
    harness.execution_gate_open = True

    with pytest.raises(RuntimeInvariantError, match="Execution Gate"):
        harness.assert_invariants()


def test_input_exactly_one_assertion_detects_ledger_divergence() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.MODEL_WAIT)
    harness.submit_input("message-1", queue_only=True)
    harness.next_queue.clear()

    with pytest.raises(RuntimeInvariantError, match="Input Exactly-One"):
        harness.assert_invariants()


def test_fifo_assertion_detects_reordered_next_turn_inputs() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.MODEL_WAIT)
    harness.submit_input("message-1", queue_only=True)
    harness.submit_input("message-2", queue_only=True)
    harness.next_queue.reverse()

    with pytest.raises(RuntimeInvariantError, match="FIFO"):
        harness.assert_invariants()


def test_cursor_assertion_detects_terminal_watermark_loss() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.MODEL_WAIT)
    harness.observe_status(ServerResult.COMPLETED, last_event_seq=8)
    harness.cursor = 7

    with pytest.raises(RuntimeInvariantError, match="Cursor"):
        harness.assert_invariants()


def test_isolation_assertion_detects_stale_identity_mutation() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.MODEL_WAIT)
    harness._stale_state_mutations = 1

    with pytest.raises(RuntimeInvariantError, match="Isolation"):
        harness.assert_invariants()


def test_terminal_cleanup_assertion_detects_leaked_resource() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.APPROVAL)
    harness.observe_status(ServerResult.INTERRUPTED, last_event_seq=9)
    harness.leases.add(RuntimeLease.APPROVAL)

    with pytest.raises(RuntimeInvariantError, match="Terminal Cleanup"):
        harness.assert_invariants()


def test_tui_atomicity_assertion_detects_thinking_content_coexistence() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.ASSISTANT_STREAMING)

    with pytest.raises(RuntimeInvariantError, match="coexist"):
        harness.append_frame(LogicalFrame(
            turn_id="turn-1",
            indicator=FrameIndicator.THINKING,
            assistant_text="hello",
            kind=FrameKind.ASSISTANT_HANDOFF,
        ))


def test_reconciliation_keeps_one_owner_for_each_message() -> None:
    harness = TurnScenarioHarness()
    harness.start_turn("turn-1", TurnPhase.APPROVAL)
    harness.submit_input("message-1")
    harness.reconcile_input("message-1", InputOwner.QUEUED_NEXT)

    assert harness.next_queue == ["message-1"]
    assert harness.inputs["message-1"].owner is InputOwner.QUEUED_NEXT
