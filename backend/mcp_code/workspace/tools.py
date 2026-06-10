# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.mcp_code.workspace.diagnostics import WorkspaceSearchDiagnostics
from backend.mcp_code.workspace.file_ops import WorkspaceFileTools
from backend.mcp_code.workspace.search import WorkspaceSearchTools


class WorkspaceTools(NativeCodingComponent):
    """提供工作区文件读取、写入、搜索和路径列表能力。"""

    def __init__(self, core: NativeCodingBase):
        super().__init__(core)

        self._files       = WorkspaceFileTools(core)
        self._diagnostics = WorkspaceSearchDiagnostics(core)

        self._search = WorkspaceSearchTools(
            core,
            diagnostics=self._diagnostics,
            symbols=getattr(core, "_repo_map", None)
        )

    def workspace_root(self):
        return self._files.workspace_root()

    def list_file(self, *args, **kwargs):
        return self._files.list_file(*args, **kwargs)

    def read_file(self, *args, **kwargs):
        return self._files.read_file(*args, **kwargs)

    def search(self, *args, **kwargs):
        return self._search.search(*args, **kwargs)

    def write_file(self, *args, **kwargs):
        return self._files.write_file(*args, **kwargs)

    def copy_file(self, *args, **kwargs):
        return self._files.copy_file(*args, **kwargs)

    def move_file(self, *args, **kwargs):
        return self._files.move_file(*args, **kwargs)

    def delete_file(self, *args, **kwargs):
        return self._files.delete_file(*args, **kwargs)


if __name__ == '__main__':
    pass
