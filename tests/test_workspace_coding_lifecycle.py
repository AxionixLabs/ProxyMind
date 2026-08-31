import asyncio
from types import SimpleNamespace

import pytest

from agent.harness.workspace_runtime import WorkspaceRuntimeOwner


class _CodingRuntime:
    def __init__(self, root, application_layout, closed) -> None:
        self.root = root
        self.application_layout = application_layout
        self.user_shell = object()
        self._closed = closed

    async def close(self) -> None:
        self._closed.append(self.root)


def _runtime_owner(closed):
    policies = []

    def create_coding(*, root, application_layout):
        return _CodingRuntime(root, application_layout, closed)

    def create_policy(*, workspace_root):
        policy = SimpleNamespace(workspace_root=workspace_root)
        policies.append(policy)
        return policy

    owner = WorkspaceRuntimeOwner(
        "workspace-a",
        application_layout=SimpleNamespace(root="application"),
        coding_factory=create_coding,
        execution_policy_factory=create_policy,
    )
    return owner, policies


@pytest.mark.anyio
async def test_workspace_replacement_updates_resources_and_retires_previous() -> None:
    closed = []
    owner, policies = _runtime_owner(closed)
    previous = owner.coding

    owner.replace("workspace-b")
    await asyncio.sleep(0)

    assert owner.coding.root == "workspace-b"
    assert owner.coding is not previous
    assert owner.user_shell is owner.coding.user_shell
    assert owner.execution_policy is policies[-1]
    assert policies[-1].workspace_root == "workspace-b"
    assert closed == ["workspace-a"]

    await owner.close()

    assert closed == ["workspace-a", "workspace-b"]


def test_workspace_replacement_without_event_loop_defers_cleanup() -> None:
    closed = []
    owner, _policies = _runtime_owner(closed)

    owner.replace("workspace-b")

    assert closed == []

    asyncio.run(owner.close())

    assert closed == ["workspace-b", "workspace-a"]


@pytest.mark.anyio
async def test_workspace_runtime_close_is_idempotent() -> None:
    closed = []
    owner, _policies = _runtime_owner(closed)

    await owner.close()
    await owner.close()

    assert closed == ["workspace-a"]
