# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from agent.harness.execution.resources import ExecutionResources


def _resources(
    *,
    client_registry_factory: Mock | None = None,
) -> tuple[ExecutionResources, SimpleNamespace]:
    client_registry = SimpleNamespace(
        list_tools=lambda: SimpleNamespace(tools=("one", "two")),
    )
    builtin_registry = SimpleNamespace(
        list_tools=lambda: SimpleNamespace(tools=()),
    )
    client_factory = client_registry_factory or Mock(
        return_value=client_registry,
    )
    runtime = SimpleNamespace(with_session=AsyncMock())
    runtime_builder = Mock(return_value=runtime)
    event_reporting = SimpleNamespace(close=AsyncMock())

    async def await_cleanup(awaitable):
        return await awaitable

    resources = ExecutionResources(
        event_reporting=event_reporting,
        tool_runtime_builder=runtime_builder,
        client_registry_factory=client_factory,
        builtin_registry_factory=Mock(return_value=builtin_registry),
        external_runtime_factory=None,
        await_cleanup=await_cleanup,
    )
    return resources, SimpleNamespace(
        client_factory=client_factory,
        client_registry=client_registry,
        event_reporting=event_reporting,
        runtime=runtime,
        sources=runtime_builder.call_args.args[0],
    )


def test_execution_resources_own_dynamic_tool_registry_snapshot() -> None:
    resources, owned = _resources()

    assert owned.client_factory.call_count == 0
    assert resources.client_tool_count() == 2
    assert owned.sources.client_registry() is owned.client_registry
    assert owned.client_factory.call_count == 1

    replacement = SimpleNamespace(
        list_tools=lambda: SimpleNamespace(tools=("replacement",)),
    )
    owned.client_factory.return_value = replacement
    resources.rebuild_client_registry()

    assert owned.sources.client_registry() is replacement
    assert owned.client_factory.call_count == 2


def test_execution_resources_own_service_profile_and_environment_snapshot() -> None:
    resources, _owned = _resources()
    environment = {"paths": ["helix"]}

    resources.link_service(environment)
    environment["paths"].append("mutated")

    assert resources.is_service_linked()
    assert resources.tool_profile_for_turn() == "app"
    assert resources.service_exec_env_snapshot() == {"paths": ["helix"]}

    resources.set_service_tool_profile("api")
    assert resources.tool_profile_for_turn() == "api"

    resources.unlink_service()
    assert not resources.is_service_linked()
    assert resources.tool_profile_for_turn() is None
    assert resources.service_exec_env_snapshot() is None


def test_execution_resources_reject_profile_change_when_service_is_unlinked() -> None:
    resources, _owned = _resources()

    with pytest.raises(RuntimeError, match="not linked"):
        resources.set_service_tool_profile("api")


@pytest.mark.anyio
async def test_execution_resources_close_owned_runtime_resources() -> None:
    resources, owned = _resources()
    resources.external_mcp.close = AsyncMock()
    resources.link_service(tool_profile="api")

    await resources.close()

    assert not resources.is_service_linked()
    owned.event_reporting.close.assert_awaited_once_with()
    resources.external_mcp.close.assert_awaited_once_with()


@pytest.mark.anyio
async def test_execution_resources_delegate_complete_tool_session_contract() -> None:
    resources, owned = _resources()
    callback = AsyncMock()
    before_user_flow = AsyncMock()
    owned.runtime.with_session.return_value = "completed"

    result = await resources.with_mcp_session(
        {"primary": {"model": "test-model"}},
        callback,
        before_user_flow=before_user_flow,
    )

    assert result == "completed"
    owned.runtime.with_session.assert_awaited_once_with(
        {"primary": {"model": "test-model"}},
        callback,
        before_user_flow=before_user_flow,
    )
