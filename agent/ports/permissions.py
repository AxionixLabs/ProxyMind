# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.permission_profiles import (
    PermissionGrantScope,
    PermissionProfile,
)

__all__ = ("PermissionGrantReader", "PermissionGrantPort")


class PermissionGrantReader(typing.Protocol):
    """提供执行上下文读取权限授权的最小端口。"""

    def has_grant(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
        environment_id: str | None,
        cwd: str,
        permissions: PermissionProfile,
    ) -> bool:
        """判断授权是否覆盖指定的执行权限。"""

    def strict_auto_review_enabled(
        self,
        *,
        cid: str,
        sid: str,
        turn_id: str,
    ) -> bool:
        """判断指定轮次是否启用严格自动审查。"""


@typing.runtime_checkable
class PermissionGrantPort(PermissionGrantReader, typing.Protocol):
    """定义执行层写入权限授权的最小端口。"""

    def grant(
        self,
        *,
        scope: PermissionGrantScope,
        cid: str,
        sid: str,
        turn_id: str,
        environment_id: str | None,
        cwd: str | None,
        permissions: PermissionProfile,
        requested_permissions: PermissionProfile,
        strict_auto_review: bool,
    ) -> None:
        """保存一项经过边界校验的权限授予。"""


if __name__ == '__main__':
    pass
