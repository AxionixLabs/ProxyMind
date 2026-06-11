# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.mcp_code.workspace.file_ops import WorkspaceFileTools


class WorkspaceTools(NativeCodingComponent):
    """提供工作区文件读写能力。"""

    def __init__(self, core: NativeCodingBase) -> None:
        """装配文件工具。"""
        super().__init__(core)

        self._files = WorkspaceFileTools(core)

    def read_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """转发文件读取请求。"""
        return self._files.read_file(*args, **kwargs)

    def write_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """转发文件写入请求。"""
        return self._files.write_file(*args, **kwargs)


if __name__ == '__main__':
    pass
