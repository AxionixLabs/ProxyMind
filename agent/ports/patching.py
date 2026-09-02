# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

__all__ = ("WorkspacePatchPort",)


class WorkspacePatchPort(typing.Protocol):
    """定义绑定工作区的文本补丁执行与本轮差异跟踪契约。"""

    agent_id: str

    def apply_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False,
    ) -> Mapping[str, typing.Any]:
        """应用补丁并返回由调用方校验的结果信封。"""
        ...

    def track_patch_delta(self, delta: dict[str, typing.Any]) -> str:
        """把一次精确文本变更合并到当前 Turn 的差异快照。"""
        ...


if __name__ == '__main__':
    pass
