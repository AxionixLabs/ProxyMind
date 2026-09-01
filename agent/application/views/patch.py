# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

PatchPhase: typing.TypeAlias = typing.Literal[
    "proposed",
    "applied",
    "failed",
]
PatchAction: typing.TypeAlias = typing.Literal[
    "add",
    "delete",
    "update",
    "rename",
]
PatchLineKind: typing.TypeAlias = typing.Literal[
    "context",
    "add",
    "remove",
]


@dataclass(frozen=True, slots=True)
class PatchLineView:
    """描述补丁中的一行及其新旧文件位置。"""

    kind: PatchLineKind
    text: str
    old_line: int | None = None
    new_line: int | None = None


@dataclass(frozen=True, slots=True)
class PatchHunkView:
    """描述补丁中连续的一组差异行。"""

    lines: tuple[PatchLineView, ...]


@dataclass(frozen=True, slots=True)
class PatchFileView:
    """描述单个文件的结构化补丁变化。"""

    action: PatchAction
    old_path: str
    new_path: str
    hunks: tuple[PatchHunkView, ...]
    added: int = 0
    removed: int = 0
    old_line_count: int = 0
    new_line_count: int = 0


@dataclass(frozen=True, slots=True)
class PatchDiagnosticView:
    """描述补丁失败时一个具名诊断字段。"""

    label: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatchView:
    """描述一次补丁调用在完整生命周期中的结构化展示。"""

    call_id: str
    phase: PatchPhase
    raw_patch: str
    files: tuple[PatchFileView, ...] = ()
    result_files: tuple[dict[str, typing.Any], ...] = ()
    diagnostics: tuple[PatchDiagnosticView, ...] = ()
    cost_ms: int | None = None


if __name__ == '__main__':
    pass
