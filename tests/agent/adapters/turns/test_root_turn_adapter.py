import pytest

from agent.adapters.turns.root import RootTurnCommandExecutor
from agent.application.turns.run_result import RunResult
from agent.domain.policies import preset_permissions
from agent.protocol import SubmitTurnCommand


@pytest.mark.anyio
async def test_root_turn_executor_maps_complete_command() -> None:
    received: dict[str, object] = {}

    async def operation(**kwargs) -> RunResult:
        received.update(kwargs)
        return RunResult(status="completed", assistant_text="done")

    permissions = preset_permissions("auto")
    command = SubmitTurnCommand.create(
        message="inspect",
        attachments=({"type": "text", "text": "context"},),
        environment_snapshot={"cwd": "D:/workspace"},
        pref_config={"model": "test-model"},
        extras={
            "metadata": {"cid": "cid_test", "sid": "sid_test"},
            "request_extras": {"priority": "normal"},
            "turn_id": "turn_test",
        },
    )
    executor = RootTurnCommandExecutor(
        operation,
        permissions=permissions,
    )

    result = await executor(command)

    assert result == RunResult(status="completed", assistant_text="done")
    assert received == {
        "message": "inspect",
        "exec_env": {"cwd": "D:/workspace"},
        "pref_config": {"model": "test-model"},
        "attachments": [{"type": "text", "text": "context"}],
        "permissions": permissions,
        "metadata": {"cid": "cid_test", "sid": "sid_test"},
        "extras": {"priority": "normal"},
        "turn_id": "turn_test",
    }


@pytest.mark.anyio
async def test_root_turn_executor_controls_empty_attachment_mapping() -> None:
    received: list[dict[str, object]] = []

    async def operation(**kwargs) -> RunResult:
        received.append(dict(kwargs))
        return RunResult(status="completed")

    command = SubmitTurnCommand.create(message="inspect")
    await RootTurnCommandExecutor(operation)(command)
    await RootTurnCommandExecutor(
        operation,
        include_empty_attachments=True,
    )(command)

    assert "attachments" not in received[0]
    assert received[1]["attachments"] == []


@pytest.mark.anyio
async def test_root_turn_executor_rejects_unknown_command() -> None:
    async def operation(**_kwargs) -> RunResult:
        return RunResult(status="completed")

    executor = RootTurnCommandExecutor(operation)

    with pytest.raises(
        TypeError,
        match="root turn command executor requires SubmitTurnCommand",
    ):
        await executor(object())
