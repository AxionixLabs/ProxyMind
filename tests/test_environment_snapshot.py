# -*- coding: utf-8 -*-

import datetime
import backend.utilities.runtime.exec_env

import pytest

from agent.capabilities import LocalEnvironmentSnapshotCapability
from mind_nova.requests.environment import (
    normalize_client_environment_snapshot,
    normalize_environment_provider,
)
from mind_nova.requests.payload import build_chat_payload


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


def test_helix_provider_keeps_tools_without_runtimes(tmp_path) -> None:
    provider = backend.utilities.runtime.exec_env.exec_env()
    snapshot = LocalEnvironmentSnapshotCapability().capture(
        cwd=tmp_path,
        workspace_root=tmp_path,
        providers={"helix": provider},
    )

    assert "runtimes" not in provider
    assert set(provider["tools"]) == {
        "adb",
        "k6",
        "ffmpeg",
        "ffprobe",
        "framix",
        "memrix",
    }
    assert snapshot["providers"]["helix"] == provider


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
        exec_env=None,
    )

    assert "exec_env" not in payload
