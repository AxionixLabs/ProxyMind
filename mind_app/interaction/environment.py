# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from pathlib import Path

from agent.application import capture_environment_snapshot
from agent.protocol.json_value import JsonValue
from agent.ports import CapabilityError
from observability import observe_exception

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
    """收集服务执行面后调用应用环境快照用例。"""
    providers: dict[str, Mapping[str, JsonValue]] = {}
    if controller.is_service_mcp_linked():
        service_environment = controller.service_exec_env_snapshot()
        if service_environment is not None:
            if not isinstance(service_environment, Mapping):
                raise TypeError("service environment snapshot must be an object")
            providers["helix"] = service_environment

    return capture_environment_snapshot(
        controller.runtime_services.environment_capability,
        cwd=cwd,
        workspace_root=workspace_root,
        providers=providers,
        on_failure=_observe_capture_failure,
    )


def _observe_capture_failure(error: CapabilityError) -> None:
    """记录环境快照能力失败并让命令继续使用无快照路径。"""
    observe_exception(
        "exec_env.capture.failed",
        error,
        level="WARNING",
    )
