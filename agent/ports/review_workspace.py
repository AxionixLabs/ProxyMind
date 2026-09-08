# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

ReviewRepositoryOperation: typing.TypeAlias = typing.Literal[
    "status",
    "diff",
    "show",
    "merge_base",
    "log",
    "list_files",
]


class ReviewWorkspaceReadError(RuntimeError):
    """表示 Review 只读能力无法安全完成请求。"""


@dataclass(frozen=True, slots=True)
class ReviewRepositoryRead:
    """保存一次受约束 Git 读取的命令事实和有界输出。"""

    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ReviewFileRead:
    """保存一次工作区文本读取的规范路径和有界行范围。"""

    path: str
    content: str
    start_line: int
    end_line: int
    total_lines: int
    truncated: bool


class WorkspaceReviewReadPort(typing.Protocol):
    """定义 Review Turn 可用的工作区只读能力及其绑定生命周期。

    实现方必须绑定单一工作区根，只执行结构化 allowlist，且不得通过 shell、
    网络、Git hook、外部 diff 或权限扩张产生工作区外效果。
    """

    agent_id: str

    async def read_repository(
        self,
        *,
        operation: ReviewRepositoryOperation,
        revision: str | None = None,
        other_revision: str | None = None,
        staged: bool = False,
        paths: tuple[str, ...] = (),
        max_count: int = 20,
    ) -> ReviewRepositoryRead:
        """执行结构化 allowlist 中的单次只读 Git 查询。"""
        ...

    async def read_file(
        self,
        *,
        path: str,
        start_line: int = 1,
        max_lines: int = 200,
    ) -> ReviewFileRead:
        """读取工作区内一个 UTF-8 文本文件的有界行范围。"""
        ...


if __name__ == '__main__':
    pass
