# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from pathlib import Path

from agent.application.services import RuntimeServices
from agent.application.turns.environment import capture_environment_snapshot
from agent.ports import CapabilityError
from agent.protocol.json_value import JsonValue
from observability import observe_exception


class TurnEnvironmentHost(typing.Protocol):
    """描述环境快照适配器所需的最小运行宿主。

    实现方持有工作区、进程级环境能力和可选的 Helix 执行环境；适配器只在一次
    Turn 登记前读取快照，不得修改宿主状态或延长能力生命周期。
    """

    history_workspace: str
    runtime_services: RuntimeServices

    def is_service_mcp_linked(self) -> bool:
        """返回当前工具会话是否包含 Helix 服务。"""
        ...

    def service_exec_env_snapshot(self) -> dict[str, typing.Any] | None:
        """返回当前 Helix 服务环境的独立快照。"""
        ...


def capture_active_turn_environment(
    host: TurnEnvironmentHost,
) -> dict[str, JsonValue] | None:
    """按宿主当前工作区捕获一个新主动 Turn 的环境快照。"""
    workspace = host.history_workspace
    return capture_turn_environment(
        host,
        cwd=workspace,
        workspace_root=workspace,
    )


def capture_turn_environment(
    host: TurnEnvironmentHost,
    *,
    cwd: str | Path,
    workspace_root: str | Path,
) -> dict[str, JsonValue] | None:
    """聚合宿主环境提供者并调用应用环境快照用例。"""
    providers: dict[str, Mapping[str, JsonValue]] = {}
    if host.is_service_mcp_linked():
        service_environment = host.service_exec_env_snapshot()
        if service_environment is not None:
            if not isinstance(service_environment, Mapping):
                raise TypeError("service environment snapshot must be an object")
            providers["helix"] = service_environment

    return capture_environment_snapshot(
        host.runtime_services.environment_capability,
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
