# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from pathlib import Path
from agent.application import (
    CapabilityError,
    EnvironmentSnapshotCapability,
    JsonValue,
)
from engine.observability import observe_exception

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


def capture_active_turn_environment(
    controller: "Mind",
) -> dict[str, JsonValue] | None:
    """按 Controller 当前工作区捕获一个新主动 Turn 的环境快照。"""
    workspace = controller.history_workspace
    return capture_turn_environment(
        controller,
        cwd=workspace,
        workspace_root=workspace,
    )


def capture_turn_environment(
    controller: "Mind",
    *,
    cwd: str | Path,
    workspace_root: str | Path,
) -> dict[str, JsonValue] | None:
    """通过注入能力捕获一个主动 Turn 的完整环境快照。"""
    runtime_services = controller.runtime_services
    capability = runtime_services.environment_capability
    if not isinstance(capability, EnvironmentSnapshotCapability):
        raise TypeError(
            "environment capability does not implement "
            "EnvironmentSnapshotCapability"
        )

    providers: dict[str, Mapping[str, JsonValue]] = {}
    if controller.is_service_mcp_linked():
        service_environment = controller.service_exec_env_snapshot()
        if service_environment is not None:
            if not isinstance(service_environment, Mapping):
                raise TypeError("service environment snapshot must be an object")
            providers["helix"] = service_environment

    try:
        snapshot = capability.capture(
            cwd=cwd,
            workspace_root=workspace_root,
            providers=providers,
        )
    except CapabilityError as error:
        observe_exception(
            "exec_env.capture.failed",
            error,
            level="WARNING",
        )
        return None

    if not isinstance(snapshot, Mapping):
        raise TypeError("environment capability must return an object")
    return dict(snapshot)


if __name__ == '__main__':
    pass
