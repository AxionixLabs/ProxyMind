import asyncio
from dataclasses import dataclass

import pytest

from agent.application import submit_turn
from agent.protocol import SubmitTurnCommand
from agent.runtime import SessionLoop


@dataclass(frozen=True, slots=True)
class _Result:
    status: str


def _command(
    message: str,
    *,
    session_id: str = "session-local",
    run_id: str | None = None,
    command_id: str | None = None,
    idempotency_key: str | None = None,
) -> SubmitTurnCommand:
    return SubmitTurnCommand.create(
        message=message,
        session_id=session_id,
        run_id=run_id,
        command_id=command_id,
        idempotency_key=idempotency_key,
    )


def test_submit_turn_command_freezes_and_serializes_payload() -> None:
    attachment = {"kind": "image", "meta": {"width": 10}}
    pref_config = {"primary": {"model": "test-model"}}
    command = SubmitTurnCommand.create(
        message="inspect",
        attachments=(attachment,),
        pref_config=pref_config,
        session_id="session-local",
        run_id="run-local",
        command_id="command-local",
    )

    attachment["meta"]["width"] = 20
    pref_config["primary"]["model"] = "changed-model"

    assert command.to_dict() == {
        "command_id": "command-local",
        "session_id": "session-local",
        "run_id": "run-local",
        "kind": "submit_turn",
        "payload": {
            "message": "inspect",
            "attachments": [{
                "kind": "image",
                "meta": {"width": 10},
            }],
            "pref_config": {"primary": {"model": "test-model"}},
        },
        "idempotency_key": "command-local",
        "causation_id": None,
        "trace_context": {},
    }


@pytest.mark.anyio
async def test_submit_turn_emits_successful_run_sequence() -> None:
    command = _command("inspect", run_id="run-success")

    async def execute(request: SubmitTurnCommand) -> _Result:
        assert request is command
        return _Result(status="completed")

    result = await submit_turn(command, execute)

    assert result.value == _Result(status="completed")
    assert [event.kind for event in result.events] == [
        "run_queued",
        "run_started",
        "run_completed",
    ]
    assert [event.sequence for event in result.events] == [1, 2, 3]
    assert {event.run_id for event in result.events} == {"run-success"}
    assert {event.causation_id for event in result.events} == {
        command.command_id
    }


@pytest.mark.anyio
async def test_session_loop_serializes_runs_and_deduplicates_command() -> None:
    first = _command(
        "first",
        run_id="run-first",
        command_id="command-first",
        idempotency_key="intent-first",
    )
    duplicate = _command(
        "first",
        run_id="run-first",
        command_id="command-first-retry",
        idempotency_key="intent-first",
    )
    second = _command(
        "second",
        run_id="run-second",
        command_id="command-second",
    )
    calls: list[str] = []

    async def execute(request: SubmitTurnCommand) -> _Result:
        calls.append(f"start:{request.message}")
        await asyncio.sleep(0)
        calls.append(f"end:{request.message}")
        return _Result(status="completed")

    session = SessionLoop("session-local", execute)
    try:
        first_result, duplicate_result, second_result = await asyncio.gather(
            session.execute(first),
            session.execute(duplicate),
            session.execute(second),
        )
    finally:
        await session.close()

    assert calls == ["start:first", "end:first", "start:second", "end:second"]
    assert duplicate_result is first_result
    assert first_result.run_id == "run-first"
    assert second_result.run_id == "run-second"
    assert [event.kind for event in session.drain_events()] == [
        "run_queued",
        "run_started",
        "run_completed",
        "run_queued",
        "run_started",
        "run_completed",
    ]


@pytest.mark.anyio
async def test_session_loop_emits_failed_tool_result() -> None:
    command = _command("use tool", run_id="run-tool-failed")

    async def execute(_request: SubmitTurnCommand) -> _Result:
        return _Result(status="failed")

    result = await submit_turn(command, execute)

    assert result.value.status == "failed"
    assert result.events[-1].kind == "run_failed"
    assert result.events[-1].payload == {"status": "failed"}


@pytest.mark.anyio
async def test_session_loop_cancellation_closes_active_run() -> None:
    command = _command("wait", run_id="run-cancelled")
    started = asyncio.Event()

    async def execute(_request: SubmitTurnCommand) -> _Result:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    session = SessionLoop("session-local", execute)
    task = asyncio.create_task(session.execute(command))
    await started.wait()
    await session.close(cancel_running=True)

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event.kind for event in session.events_for(command.run_id)] == [
        "run_queued",
        "run_started",
        "run_cancelled",
    ]
