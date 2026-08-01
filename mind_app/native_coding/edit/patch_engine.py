# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.native_coding.base import (
    NativeCodingBase,
    NativeCodingComponent
)
from mind_app.native_coding.edit.apply import PatchApplier
from mind_app.native_coding.edit.diagnostics import PatchDiagnostics
from mind_app.native_coding.edit.operations import TextPatchOperations
from mind_app.native_coding.edit.parser import PatchParser
from mind_app.native_coding.edit.planning import PatchPlanner


class PatchEngine(NativeCodingComponent):
    """提供工作区文本补丁解析、校验、应用和诊断能力。"""

    def __init__(self, core: NativeCodingBase) -> None:
        """装配文本补丁子组件。"""
        super().__init__(core)

        self._diagnostics = PatchDiagnostics(core)
        self._parser      = PatchParser()

        self._applier = PatchApplier(
            core,
            diagnostics=self._diagnostics
        )

        self._planner = PatchPlanner(
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
        """转发文本补丁请求。"""
        return self._operations.apply_patch(*args, **kwargs)


if __name__ == '__main__':
    pass

