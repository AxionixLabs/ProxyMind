import base64
from dataclasses import replace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from agent.application.approvals.factory import build_approval_request
from agent.application.approvals.legacy import domain_action_from_request
from agent.application.approvals.models import ApprovalOutcome
from agent.application.approvals.presentation import ToolApprovalPresentation
from agent.application.tools.authorization import ToolTurnInterrupted
from agent.application.tools.computer import computer_tools
from agent.application.tools.context import ToolHandlerContext
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.domain.approvals import (
    ApprovalActionKind,
    ApprovalDecisionKind,
    DesktopApprovalAction,
    allowed_decisions,
)
from agent.domain.policies import preset_permissions
from agent.ports.approvals import ApprovalCoordinatorPort
from agent.ports.desktop import (
    DesktopBounds,
    DesktopExecutionPort,
    DesktopImage,
    DesktopInput,
    DesktopInputResult,
    DesktopKey,
    DesktopObservation,
    DesktopWindows,
)
from agent.ports.mcp_session import McpSessionPort
from agent.protocol.json_value import ThawedJsonValue


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")


def observation() -> DesktopObservation:
    return DesktopObservation("window", "observation", "2026-11-05T00:00:00Z", DesktopBounds(-100, 20, 1, 1),
        1, "physical_screen_pixels", (), False, None, DesktopImage(1, 1, PNG, "image/png"))


def context(*, full_access: bool = False) -> ToolHandlerContext:
    turn = TurnContext.create(agent=AgentContext.root("sid_test"), cid="cid_test", sid="sid_test", source="test",
        pref_config={}, cwd="/workspace", permissions=preset_permissions("full-access" if full_access else "auto"),
        turn_id="turn_test")
    return ToolHandlerContext(Mock(spec=McpSessionPort), turn, {}, call_id="call_test")


def approval(decision: str) -> AsyncMock:
    coordinator = AsyncMock(spec=ApprovalCoordinatorPort)
    coordinator.request_outcome.return_value = ApprovalOutcome.create(decision, source="user", reason="user")
    return coordinator


@pytest.mark.anyio
@pytest.mark.parametrize("arguments", [
    {}, {"action": "click"}, {"action": "list_windows", "window": "window"},
    {"action": "observe", "window": "window", "screenshot": 1},
    {"action": "observe", "window": "", "screenshot": True},
    {"action": "validate_observation", "window": "window", "observation": "x" * 65},
    {"action": "list_windows", "execution": {"approved": True}},
])
async def test_invalid_arguments_never_prompt_or_execute(arguments: dict[str, ThawedJsonValue]) -> None:
    executor, coordinator = AsyncMock(spec=DesktopExecutionPort), approval("accept")
    result = await computer_tools(executor, approval_coordinator=coordinator)[0].handler(arguments, context())
    assert not result.ok and result.data["error"] == "invalid_arguments"
    coordinator.request_outcome.assert_not_called()
    executor.execute.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("fields", [
    {"action": "click", "target": {"element": True}, "button": "left", "count": 1},
    {"action": "click", "target": {"x": 10, "y": 10, "element": 2}, "button": "left", "count": 1},
    {"action": "move", "target": {"element": 1}, "duration_ms": 100},
    {"action": "drag", "start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 1}, "duration_ms": 49},
    {"action": "scroll", "target": {"x": 1, "y": 1}, "delta_x": 0, "delta_y": 0},
    {"action": "press_key", "keys": ["CTRL", "CTRL"]},
    {"action": "press_key", "keys": ["F01"]},
    {"action": "type_text", "text": "🐱" * 1025},
    {"action": "type_text", "text": "private\0text"},
])
async def test_invalid_input_never_reaches_approval_or_runtime(fields: dict[str, ThawedJsonValue]) -> None:
    executor, coordinator = AsyncMock(spec=DesktopExecutionPort), approval("accept")
    result = await computer_tools(executor, approval_coordinator=coordinator)[0].handler(
        {"window": "window", "observation": "observation", **fields}, context())
    assert not result.ok and result.data["error"] == "invalid_arguments"
    coordinator.request_outcome.assert_not_called()
    executor.execute.assert_not_called()


@pytest.mark.anyio
async def test_input_approval_binds_exact_keys_and_reports_submission() -> None:
    executor, coordinator = AsyncMock(spec=DesktopExecutionPort), approval("accept")
    executor.execute.return_value = DesktopInputResult(4, True)
    result = await computer_tools(executor, approval_coordinator=coordinator)[0].handler(
        {"action": "press_key", "window": "window", "observation": "observation", "keys": ["CTRL", "A"]}, context())
    payload = coordinator.request_outcome.call_args.args[0]
    action = domain_action_from_request(build_approval_request(payload))
    assert isinstance(action, DesktopApprovalAction) and action.operation == "press_key"
    assert executor.execute.call_args.args[1] == DesktopInput("window", "observation", DesktopKey(("CTRL", "A")))
    assert result.ok and result.data == {"eventsSubmitted": 4, "requiresObservation": True}


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["decline", "cancel"])
async def test_denial_prevents_native_execution(decision: str) -> None:
    executor = AsyncMock(spec=DesktopExecutionPort)
    result = await computer_tools(executor, approval_coordinator=approval(decision))[0].handler({"action": "list_windows"}, context())
    assert not result.ok
    executor.execute.assert_not_called()


@pytest.mark.anyio
async def test_cancel_uses_current_call_interrupt() -> None:
    executor = AsyncMock(spec=DesktopExecutionPort)
    runtime = context()
    runtime.interrupt_turn = AsyncMock(return_value=True)
    with pytest.raises(ToolTurnInterrupted):
        await computer_tools(executor, approval_coordinator=approval("cancel"))[0].handler({"action": "list_windows"}, runtime)
    runtime.interrupt_turn.assert_awaited_once_with("call_test")
    executor.execute.assert_not_called()


@pytest.mark.anyio
async def test_desktop_approval_uses_local_kind_and_complete_identity() -> None:
    executor, coordinator = AsyncMock(spec=DesktopExecutionPort), approval("accept")
    executor.execute.return_value = observation()
    args: dict[str, ThawedJsonValue] = {"action": "observe", "window": "window", "screenshot": True}
    result = await computer_tools(executor, approval_coordinator=coordinator)[0].handler(args, context())
    payload = coordinator.request_outcome.call_args.args[0]
    request = build_approval_request(payload)
    action = domain_action_from_request(request)
    assert isinstance(action, DesktopApprovalAction) and action.operation == "observe"
    assert isinstance(request.presentation, ToolApprovalPresentation)
    assert "observe" in request.presentation.operations[0]
    assert action.identity.session_id == "sid_test" and action.identity.run_id == "turn_test"
    assert action.execution.tool_call_id == "call_test"
    assert request.decisions == ("accept", "decline", "cancel")
    assert ApprovalDecisionKind.ALLOW_FOR_SESSION not in allowed_decisions(ApprovalActionKind.DESKTOP)
    changed = build_approval_request({**payload, "arguments": {**args, "window": "other"}})
    assert domain_action_from_request(changed).fingerprint != action.fingerprint
    assert result.ok and result.data["observation"] == "observation"
    assert len(result.attachments) == 1


@pytest.mark.anyio
async def test_approval_policy_and_reset_do_not_infer_workspace_authorization() -> None:
    executor = AsyncMock(spec=DesktopExecutionPort)
    executor.execute.return_value = DesktopWindows((), False)
    coordinator = approval("accept")
    tool = computer_tools(executor, approval_coordinator=coordinator)[0]
    runtime = context()
    runtime.turn_context = replace(runtime.turn_context, permissions=replace(runtime.turn_context.permissions, approval_policy="never"))
    denied = await tool.handler({"action": "list_windows"}, runtime)
    assert denied.data["error"] == "approval_required"
    executor.execute.assert_not_called()
    allowed = await tool.handler({"action": "list_windows"}, context(full_access=True))
    assert allowed.ok
    coordinator.request_outcome.assert_not_called()
    reset = await tool.handler({"action": "reset"}, runtime)
    assert reset.ok
    executor.reset_session.assert_awaited_once_with("sid_test")


@pytest.mark.anyio
async def test_missing_approval_identity_fails_closed() -> None:
    runtime = context(full_access=True)
    runtime.call_id = None
    executor = AsyncMock(spec=DesktopExecutionPort)
    result = await computer_tools(executor, approval_coordinator=None)[0].handler({"action": "list_windows"}, runtime)
    assert result.data["error"] == "approval_identity_missing"
    executor.execute.assert_not_called()
