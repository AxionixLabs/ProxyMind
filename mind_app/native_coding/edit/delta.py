# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing


class AppliedPatchChange:
    """记录一次 apply_patch 已提交的文本变更。"""

    __slots__ = (
        "path",
        "action",
        "old_content",
        "new_content",
        "source_path",
        "overwritten_content"
    )

    def __init__(
        self,
        *,
        path: str,
        action: str,
        old_content: str | None = None,
        new_content: str | None = None,
        source_path: str | None = None,
        overwritten_content: str | None = None
    ) -> None:
        """初始化单个文件变更记录。"""
        self.path = path
        self.action = action
        self.old_content = old_content
        self.new_content = new_content
        self.source_path = source_path
        self.overwritten_content = overwritten_content

    def payload(self) -> dict[str, typing.Any]:
        """返回可序列化的变更载荷。"""
        return {
            "path"                : self.path,
            "action"              : self.action,
            "old_content"         : self.old_content,
            "new_content"         : self.new_content,
            "source_path"         : self.source_path,
            "overwritten_content" : self.overwritten_content
        }


class AppliedPatchDelta:
    """记录一次 apply_patch 已提交变更及其精确性。"""

    __slots__ = ("exact", "changes")

    def __init__(
        self,
        *,
        exact: bool = True,
        changes: list[AppliedPatchChange] | None = None
    ) -> None:
        """初始化 patch delta。"""
        self.exact = bool(exact)
        self.changes = list(changes or [])

    def mark_inexact(self) -> None:
        """标记当前 delta 不再能保证精确。"""
        self.exact = False

    def add_change(
        self,
        *,
        path: str,
        action: str,
        old_content: str | None,
        new_content: str | None,
        source_path: str | None = None,
        overwritten_content: str | None = None,
        exact: bool = True
    ) -> None:
        """追加一个已成功提交的文件变更。"""
        if not exact:
            self.mark_inexact()
        self.changes.append(
            AppliedPatchChange(
                path=path,
                action=action,
                old_content=old_content,
                new_content=new_content,
                source_path=source_path,
                overwritten_content=overwritten_content
            )
        )

    def add_planned_change(self, item: dict[str, typing.Any]) -> None:
        """从已成功写入的计划项追加变更。"""
        self.add_change(
            path=str(item.get("path") or ""),
            action=str(item.get("action") or ""),
            old_content=item.get("old_content"),
            new_content=item.get("new_content"),
            source_path=item.get("source_path"),
            overwritten_content=item.get("overwritten_content"),
            exact=bool(item.get("delta_exact", True))
        )

    def payload(self) -> dict[str, typing.Any]:
        """返回可放入工具结果的 delta 载荷。"""
        return {
            "exact"   : self.exact,
            "changes" : [change.payload() for change in self.changes]
        }


if __name__ == '__main__':
    pass
