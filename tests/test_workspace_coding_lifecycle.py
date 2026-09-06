import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        image_reader_factory=lambda root: SimpleNamespace(root=root),
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
    assert owner.image_reader.root == "workspace-b"
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


@pytest.mark.anyio
async def test_workspace_close_releases_both_process_capabilities_on_failure() -> None:
    closed = []
    process_capability = AsyncMock()
    interactive_process_capability = AsyncMock()
    process_capability.aclose.side_effect = RuntimeError("pipe close failed")

    def create_coding(
        *,
        root,
        application_layout,
        process_capability,
        interactive_process_capability,
    ):
        return _CodingRuntime(root, application_layout, closed)

    owner = WorkspaceRuntimeOwner(
        "workspace-a",
        application_layout=SimpleNamespace(root="application"),
        coding_factory=create_coding,
        execution_policy_factory=(
            lambda *, workspace_root: SimpleNamespace(workspace_root=workspace_root)
        ),
        image_reader_factory=lambda root: SimpleNamespace(root=root),
        process_capability=process_capability,
        interactive_process_capability=interactive_process_capability,
    )

    with pytest.raises(RuntimeError, match="pipe close failed"):
        await owner.close()

    process_capability.aclose.assert_awaited_once_with()
    interactive_process_capability.aclose.assert_awaited_once_with()
