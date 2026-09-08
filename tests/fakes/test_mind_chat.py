"""验证 Fake Mind Chat 的端口、故障计划与事实记录契约。"""

import pytest

from agent.ports import ProtocolCommandClient
from agent.ports import ProtocolCommandError
from tests.fakes.mind_chat import CommandFault
from tests.fakes.mind_chat import FakeEventKind
from tests.fakes.mind_chat import FakeEventSpec
from tests.fakes.mind_chat import FakeMindChatFaultPlan
from tests.fakes.mind_chat import FakeMindChatServer
from tests.fakes.mind_chat import SteerBehavior
from tests.fakes.mind_chat import ToolResultBehavior


def test_fake_mind_chat_implements_the_protocol_command_port() -> None:
    """确保 Fake 与生产命令端口保持结构一致。"""
    assert isinstance(FakeMindChatServer(), ProtocolCommandClient)


@pytest.mark.anyio
async def test_fault_plan_composes_commands_tools_and_event_trace() -> None:
    """确保不同故障维度可在一个不可变计划中组合。"""
    plan = FakeMindChatFaultPlan(
        interrupt=CommandFault.RESPONSE_LOST,
        steer=SteerBehavior.PENDING,
        tool_result=ToolResultBehavior.COMMIT_RESPONSE_LOST,
        events=(
            FakeEventSpec(FakeEventKind.ASSISTANT_DELTA, 3, text="delta"),
            FakeEventSpec(FakeEventKind.STREAM_CLOSED, 4),
        ),
    )
    server = FakeMindChatServer(fault_plan=plan)
    await server.post_mind_chat(turn_id="turn-plan", initial_event_seq=0)
    await server.emit_fault_events()

    events = [event async for event in server.stream_events()]

    assert [event.kind for event in events] == [
        FakeEventKind.TURN_STARTED,
        FakeEventKind.ASSISTANT_DELTA,
    ]
    assert server.fault_plan == plan
    assert "event_seq=3" in server.trace_text()


@pytest.mark.parametrize(
    ("behavior", "received"),
    (
        (ToolResultBehavior.RESPONSE_LOST, False),
        (ToolResultBehavior.COMMIT_RESPONSE_LOST, True),
    ),
)
@pytest.mark.anyio
async def test_tool_result_loss_reports_authoritative_status(
    behavior: ToolResultBehavior,
    received: bool,
) -> None:
    """确保丢失回执后可区分未提交与已提交结果。"""
    server = FakeMindChatServer(
        fault_plan=FakeMindChatFaultPlan(tool_result=behavior),
    )
    await server.post_mind_chat(turn_id="turn-tool", initial_event_seq=0)

    with pytest.raises(ProtocolCommandError, match="response was lost"):
        await server.post_tool_result(
            server.cid,
            server.sid,
            "call-tool",
            "local_tool",
            True,
            {"text": "done"},
            request_id="request-tool",
        )

    status = await server.get_tool_result_status(
        cid=server.cid,
        sid=server.sid,
        call_id="call-tool",
    )

    assert status["tool_status"] == (
        "result_received" if received else "missing"
    )
    assert status["result_received"] is received
    assert status["execution_deadline_at"] is None
    assert "expires_at" not in status

    if received:
        await server.post_tool_result(
            server.cid,
            server.sid,
            "call-tool",
            "local_tool",
            True,
            {"text": "done"},
            request_id="request-tool",
        )
        assert server.facts[-1].status == "duplicate"


@pytest.mark.anyio
async def test_tool_result_is_immutable_after_acceptance() -> None:
    """确保 Fake 不允许第二个请求覆盖已接收工具结果。"""
    server = FakeMindChatServer()
    await server.post_mind_chat(turn_id="turn-tool", initial_event_seq=0)
    await server.post_tool_result(
        server.cid,
        server.sid,
        "call-tool",
        "local_tool",
        True,
        {"text": "done"},
        request_id="request-first",
    )

    with pytest.raises(ProtocolCommandError, match="cannot overwrite"):
        await server.post_tool_result(
            server.cid,
            server.sid,
            "call-tool",
            "local_tool",
            False,
            {"text": "changed"},
            request_id="request-second",
        )

    status = await server.get_tool_result_status(
        cid=server.cid,
        sid=server.sid,
        call_id="call-tool",
    )

    assert status["result_received"] is True
    assert status["request_id"] == "request-first"
