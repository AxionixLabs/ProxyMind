# -*- coding: utf-8 -*-

import typing


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
        permissions: typing.Any,
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


__all__ = ("PermissionGrantReader",)


if __name__ == '__main__':
    pass
