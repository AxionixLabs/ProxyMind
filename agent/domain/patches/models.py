# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field,
)

PatchAction = typing.Literal["create", "modify", "delete", "rename"]
PatchMarker = typing.Literal[" ", "+", "-"]


@dataclass(slots=True)
class PatchLine:
    """表示补丁 hunk 中的一行。"""
    marker: PatchMarker
    text: str
    no_newline: bool = False


@dataclass(slots=True)
class PatchHunk:
    """表示带位置和行内容的补丁块。"""
    header: str = "@@"
    old_start: int = 1
    new_start: int = 1
    old_count: int = 0
    new_count: int = 0
    declared_old_count: int = 0
    declared_new_count: int = 0
    count_corrected: bool = False
    has_declared_position: bool = False
    entries: list[PatchLine] = field(default_factory=list)


@dataclass(slots=True)
class PatchFile:
    """表示一个文件的补丁动作和内容块。"""
    path: str
    old_path: str
    new_path: str
    action: PatchAction
    hunks: list[PatchHunk] = field(default_factory=list)


class PatchParseSuccess(typing.TypedDict):
    ok: typing.Literal[True]
    files: list[PatchFile]


class PatchParseFailure(typing.TypedDict):
    ok: typing.Literal[False]
    reason: str
    data: dict[str, typing.Any]


PatchParseResult = PatchParseSuccess | PatchParseFailure

if __name__ == '__main__':
    pass
