import asyncio
from dataclasses import dataclass

import pytest

from agent.application import (
    TurnApplication,
    project_run_result,
    submit_turn,
)
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand,
)
from agent.runtime import SessionLoop


@dataclass(frozen=True, slots=True)
class _Result:
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "assistant_text": "",
            "usage": {},
            "error": "tool failed" if self.status == "failed" else None,
            "exit_code": 0 if self.status == "completed" else 1,
        }


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
    extras = {"selection": {"line": 8}}
    command = SubmitTurnCommand.create(
        message="inspect",
        attachments=(attachment,),
        pref_config=pref_config,
        extras=extras,
        session_id="session-local",
        run_id="run-local",
        command_id="command-local",
    )

    attachment["meta"]["width"] = 20
    pref_config["primary"]["model"] = "changed-model"
    extras["selection"]["line"] = 9

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
            "extras": {"selection": {"line": 8}},
        },
        "idempotency_key": "command-local",
        "causation_id": None,
        "trace_context": {},
    }
    assert command.extras_value() == {"selection": {"line": 8}}


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
    assert result.projection.status == "completed"
    assert result.projection.exit_code == 0
    assert result.projection.result == result.value.to_dict()


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
async def test_turn_application_reuses_session_queue_across_submissions() -> None:
    application: TurnApplication[_Result] = TurnApplication()
    first = _command("first", run_id="run-app-first")
    second = _command("second", run_id="run-app-second")
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls: list[str] = []

    async def execute_first(_request: SubmitTurnCommand) -> _Result:
        calls.append("first:start")
        first_started.set()
        await release_first.wait()
        calls.append("first:end")
        return _Result(status="completed")

    async def execute_second(_request: SubmitTurnCommand) -> _Result:
        calls.append("second")
        return _Result(status="completed")

    first_task = asyncio.create_task(application.submit(first, execute_first))
    await first_started.wait()
    second_task = asyncio.create_task(application.submit(second, execute_second))
    await asyncio.sleep(0)

    assert calls == ["first:start"]

    release_first.set()
    first_result, second_result = await asyncio.gather(
        first_task,
        second_task,
    )
    await application.close()

    assert calls == ["first:start", "first:end", "second"]
    assert first_result.projection.status == "completed"
    assert second_result.projection.status == "completed"
    assert [event.sequence for event in first_result.events] == [1, 2, 3]
    assert [event.sequence for event in second_result.events] == [1, 2, 3]
    assert application.closed


@pytest.mark.anyio
async def test_turn_application_cancellation_rebuilds_session_loop() -> None:
    application: TurnApplication[_Result] = TurnApplication()
    cancelled = _command("wait", run_id="run-app-cancelled")
    resumed = _command("resume", run_id="run-app-resumed")
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def execute_cancelled(_request: SubmitTurnCommand) -> _Result:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        raise AssertionError("unreachable")

    async def execute_resumed(_request: SubmitTurnCommand) -> _Result:
        return _Result(status="completed")

    cancelled_task = asyncio.create_task(
        application.submit(cancelled, execute_cancelled)
    )
    await started.wait()
    cancelled_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cancelled_task

    assert cleaned.is_set()

    result = await application.submit(resumed, execute_resumed)
    await application.close()

    assert result.projection.status == "completed"


@pytest.mark.anyio
async def test_turn_application_close_waits_for_active_session() -> None:
    application: TurnApplication[_Result] = TurnApplication()
    command = _command("wait", run_id="run-app-close")
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(_request: SubmitTurnCommand) -> _Result:
        started.set()
        await release.wait()
        return _Result(status="completed")

    submit_task = asyncio.create_task(application.submit(command, execute))
    await started.wait()
    first_close = asyncio.create_task(application.close())
    second_close = asyncio.create_task(application.close())
    await asyncio.sleep(0)

    assert not first_close.done()
    assert not second_close.done()

    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close
    assert not second_close.done()

    release.set()
    result = await submit_task
    await second_close

    assert result.projection.status == "completed"
    assert application.closed


@pytest.mark.anyio
async def test_session_loop_emits_failed_tool_result() -> None:
    command = _command("use tool", run_id="run-tool-failed")

    async def execute(_request: SubmitTurnCommand) -> _Result:
        return _Result(status="failed")

    result = await submit_turn(command, execute)

    assert result.value.status == "failed"
    assert result.events[-1].kind == "run_failed"
    assert result.events[-1].payload == {
        "status": "failed",
        "result": result.value.to_dict(),
    }
    assert result.projection.status == "failed"
    assert result.projection.exit_code == 1


@pytest.mark.anyio
async def test_session_loop_rejects_inconsistent_turn_result() -> None:
    command = _command("invalid", run_id="run-invalid")

    class _InconsistentResult:
        status = "completed"

        @staticmethod
        def to_dict() -> dict[str, object]:
            return {
                "status": "failed",
                "exit_code": 1,
            }

    async def execute(_request: SubmitTurnCommand) -> _InconsistentResult:
        return _InconsistentResult()

    session = SessionLoop("session-local", execute)
    try:
        with pytest.raises(
            ValueError,
            match="payload status does not match result status",
        ):
            await session.execute(command)
    finally:
        await session.close()

    events = session.events_for(command.run_id)
    assert [event.kind for event in events] == [
        "run_queued",
        "run_started",
        "run_failed",
    ]
    assert events[-1].payload["error"]["type"] == "ValueError"


def test_run_result_projection_rejects_inconsistent_exit_code() -> None:
    terminal = RunEvent.create(
        sequence=1,
        session_id="session-local",
        run_id="run-invalid",
        kind="run_completed",
        payload={
            "status": "completed",
            "result": {
                "status": "completed",
                "exit_code": 1,
            },
        },
        causation_id="command-local",
    )

    with pytest.raises(
        ValueError,
        match="terminal event exit_code is inconsistent",
    ):
        project_run_result((terminal,))


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
