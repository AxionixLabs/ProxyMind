# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

HookViewPhase: typing.TypeAlias = typing.Literal[
    "started",
    "completed",
]
HookViewStatus: typing.TypeAlias = typing.Literal[
    "running",
    "completed",
    "failed",
    "blocked",
    "stopped",
]
HookOutputKind: typing.TypeAlias = typing.Literal[
    "warning",
    "stop",
    "feedback",
    "context",
    "error",
]


@dataclass(frozen=True, slots=True)
class HookOutputView:
    """描述一次 Hook 运行产生的展示条目。"""

    kind: HookOutputKind
    text: str


@dataclass(frozen=True, slots=True)
class HookRunView:
    """描述一次 Hook 运行的结构化生命周期。"""

    id: str
    hook_key: str
    event: str
    phase: HookViewPhase
    status: HookViewStatus
    status_message: str = ""
    duration_ms: int | None = None
    entries: tuple[HookOutputView, ...] = ()
    handler_type: str = "command"
    execution_mode: str = "sync"
    scope: str = "turn"
    source_path: str | None = None
    source: str = ""
    display_order: int = 0


if __name__ == '__main__':
    pass
