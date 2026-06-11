# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.mcp_code.workspace.file_ops import WorkspaceFileTools


class WorkspaceTools(NativeCodingComponent):
    """提供工作区文件读取和写入能力。"""

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

    def copy_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """转发文件复制请求。"""
        return self._files.copy_file(*args, **kwargs)

    def move_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """转发文件移动请求。"""
        return self._files.move_file(*args, **kwargs)

    def delete_file(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """转发文件删除请求。"""
        return self._files.delete_file(*args, **kwargs)


if __name__ == '__main__':
    pass
