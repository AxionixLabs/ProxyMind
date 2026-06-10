# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.mcp_code.edit.apply import UnifiedPatchApplier
from backend.mcp_code.edit.diagnostics import PatchDiagnostics
from backend.mcp_code.edit.operations import TextPatchOperations
from backend.mcp_code.edit.parser import UnifiedPatchParser
from backend.mcp_code.edit.planning import UnifiedPatchPlanner


class PatchEngine(NativeCodingComponent):
    """提供工作区文本补丁解析、校验、应用和诊断能力。"""

    def __init__(self, core: NativeCodingBase) -> None:
        """装配文本补丁和 unified patch 子组件。"""
        super().__init__(core)

        self._diagnostics = PatchDiagnostics(core)

        self._parser = UnifiedPatchParser(
            core,
            diagnostics=self._diagnostics
        )
        self._applier = UnifiedPatchApplier(
            core,
            diagnostics=self._diagnostics
        )
        self._planner = UnifiedPatchPlanner(
            core,
            parser=self._parser,
            applier=self._applier,
            diagnostics=self._diagnostics
        )
        self._operations = TextPatchOperations(
            core,
            planner=self._planner,
            diagnostics=self._diagnostics
        )

    def apply_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """转发精确文本替换补丁请求。"""
        return self._operations.apply_patch(*args, **kwargs)

    def apply_unified_patch(self, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
        """转发 unified diff 补丁请求。"""
        return self._operations.apply_unified_patch(*args, **kwargs)


if __name__ == '__main__':
    pass
