# -*- coding: utf-8 -*-

import datetime
from types import SimpleNamespace

import pytest

from agent.application.turns.environment import capture_environment_snapshot
from agent.capabilities import LocalEnvironmentSnapshotCapability
from agent.ports import CapabilityError
from infrastructure.services.turn_environment import (
    capture_active_turn_environment,
)
from protocol.schema.environment import (
    normalize_client_environment_snapshot,
    normalize_environment_provider,
)
from protocol.client.payload import build_chat_payload


def _snapshot() -> dict:
    """构造固定的完整环境快照。"""
    return {
        "snapshot_id": "envsnap_test_snapshot",
        "source": "client",
        "captured_at": "2026-08-29T12:00:00Z",
        "environment_id": "local",
        "cwd": "D:\\PycharmProjects\\ProxyMind",
        "status": "available",
        "status_detail": None,
        "shell": {
            "name": "powershell",
            "syntax": "powershell",
            "executable": "pwsh.exe",
            "prefix": ["pwsh.exe", "-Command"],
        },
        "workspace": {
            "root": "D:\\PycharmProjects\\ProxyMind",
            "allowed_roots": [],
            "source": "client",
        },
        "tools": {},
        "providers": {},
        "extensions": {},
    }


def test_runtime_snapshot_has_stable_protocol_shape() -> None:
    capability = LocalEnvironmentSnapshotCapability()
    snapshot = capability.capture(cwd=".", workspace_root=".")
    captured_at = snapshot["captured_at"].replace("Z", "+00:00")

    assert snapshot["snapshot_id"].startswith("envsnap_")
    assert datetime.datetime.fromisoformat(captured_at).utcoffset() is not None
    assert snapshot["source"] == "client"
    assert snapshot["status"] in {"available", "starting", "unavailable"}
    assert set(snapshot) == {
        "snapshot_id",
        "source",
        "captured_at",
        "environment_id",
        "cwd",
        "status",
        "status_detail",
        "shell",
        "workspace",
        "tools",
        "providers",
        "extensions",
    }
    assert "schema_version" not in snapshot
    assert "platform" not in snapshot
    assert "runtimes" not in snapshot
    assert "env" not in snapshot


def test_runtime_snapshot_separates_cwd_and_workspace_root(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    current_directory = workspace / "src"
    current_directory.mkdir(parents=True)

    snapshot = LocalEnvironmentSnapshotCapability().capture(
        cwd=current_directory,
        workspace_root=workspace,
    )

    assert snapshot["cwd"] == str(current_directory.resolve())
    assert snapshot["workspace"]["root"] == str(workspace.resolve())


def test_application_environment_capture_reports_capability_failure() -> None:
    class _FailingCapability:
        def capture(self, *, cwd, workspace_root, providers=None):
            raise CapabilityError(
                "environment_capture_failed",
                "unavailable",
                retryable=True,
            )

        def clear_cache(self) -> None:
            return None

    failures: list[CapabilityError] = []

    snapshot = capture_environment_snapshot(
        _FailingCapability(),
        cwd=".",
        workspace_root=".",
        on_failure=failures.append,
    )

    assert snapshot is None
    assert [failure.code for failure in failures] == [
        "environment_capture_failed"
    ]


def test_application_environment_capture_rejects_invalid_result() -> None:
    class _InvalidCapability:
        def capture(self, *, cwd, workspace_root, providers=None):
            return []

        def clear_cache(self) -> None:
            return None

    with pytest.raises(TypeError, match="must return an object"):
        capture_environment_snapshot(
            _InvalidCapability(),
            cwd=".",
            workspace_root=".",
        )


def test_turn_environment_adapter_includes_linked_service_provider(tmp_path) -> None:
    class _RecordingCapability:
        def capture(self, *, cwd, workspace_root, providers=None):
            return {
                "cwd": str(cwd),
                "workspace_root": str(workspace_root),
                "providers": dict(providers or {}),
            }

        def clear_cache(self) -> None:
            return None

    provider = {"tools": {"shell": {"version": "1"}}}
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        runtime_services=SimpleNamespace(
            environment_capability=_RecordingCapability(),
        ),
        execution=SimpleNamespace(
            is_service_linked=lambda: True,
            service_exec_env_snapshot=lambda: provider,
        ),
    )

    snapshot = capture_active_turn_environment(host)

    assert snapshot == {
        "cwd": str(tmp_path),
        "workspace_root": str(tmp_path),
        "providers": {"helix": provider},
    }


def test_turn_environment_adapter_rejects_invalid_service_provider(tmp_path) -> None:
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        runtime_services=SimpleNamespace(environment_capability=object()),
        execution=SimpleNamespace(
            is_service_linked=lambda: True,
            service_exec_env_snapshot=lambda: [],
        ),
    )

    with pytest.raises(TypeError, match="service environment snapshot"):
        capture_active_turn_environment(host)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda snapshot: snapshot.update({"schema_version": 1}), "unknown fields"),
        (lambda snapshot: snapshot.update({"platform": {}}), "unknown fields"),
        (lambda snapshot: snapshot.update({"runtimes": {}}), "unknown fields"),
        (lambda snapshot: snapshot.update({"env": {}}), "unknown fields"),
        (lambda snapshot: snapshot.update({"source": "server"}), "source must be client"),
        (lambda snapshot: snapshot.update({"captured_at": "2026-08-29"}), "timezone"),
        (
            lambda snapshot: snapshot["workspace"].update({"projects": {}}),
            "unknown fields",
        ),
    ),
)
def test_environment_snapshot_rejects_removed_or_invalid_fields(
    mutation,
    message,
) -> None:
    snapshot = _snapshot()
    mutation(snapshot)

    with pytest.raises((TypeError, ValueError), match=message):
        normalize_client_environment_snapshot(snapshot)


def test_environment_provider_rejects_removed_runtimes() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        normalize_environment_provider({
            "runtimes": {},
            "tools": {},
            "extensions": {},
        })


@pytest.mark.anyio
async def test_chat_payload_omits_unavailable_environment_snapshot() -> None:
    payload = await build_chat_payload(
        {},
        "inspect",
        [],
        session_mode="existing",
        exec_env=None,
    )

    assert "exec_env" not in payload
