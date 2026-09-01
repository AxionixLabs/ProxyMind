# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from collections.abc import (
    Callable,
    Mapping,
)
from agent.ports import (
    CapabilityError,
    EnvironmentSnapshotCapability,
)
from agent.protocol.json_value import JsonValue

SnapshotFailureObserver: typing.TypeAlias = Callable[[CapabilityError], None]


def capture_environment_snapshot(
    capability: EnvironmentSnapshotCapability,
    *,
    cwd: str | Path,
    workspace_root: str | Path,
    providers: Mapping[str, Mapping[str, JsonValue]] | None = None,
    on_failure: SnapshotFailureObserver | None = None,
) -> dict[str, JsonValue] | None:
    """通过环境能力捕获一个主动 Turn 使用的不可变快照。"""
    if not isinstance(capability, EnvironmentSnapshotCapability):
        raise TypeError(
            "environment capability does not implement "
            "EnvironmentSnapshotCapability"
        )

    try:
        snapshot = capability.capture(
            cwd=cwd,
            workspace_root=workspace_root,
            providers=providers,
        )
    except CapabilityError as error:
        if on_failure is not None:
            on_failure(error)
        return None

    if not isinstance(snapshot, Mapping):
        raise TypeError("environment capability must return an object")
    return dict(snapshot)


if __name__ == '__main__':
    pass
