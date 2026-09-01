# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import difflib
import typing


class AppliedPatchChange:
    """记录一次 apply_patch 已提交的文本变更。"""

    __slots__ = (
        "path",
        "action",
        "old_content",
        "new_content",
        "source_path",
        "overwritten_content",
        "hunks",
    )

    def __init__(
        self,
        *,
        path: str,
        action: str,
        old_content: str | None = None,
        new_content: str | None = None,
        source_path: str | None = None,
        overwritten_content: str | None = None,
        hunks: list[dict[str, typing.Any]] | None = None,
    ) -> None:
        """初始化单个文件变更记录。"""
        self.path = path
        self.action = action
        self.old_content = old_content
        self.new_content = new_content
        self.source_path = source_path
        self.overwritten_content = overwritten_content
        self.hunks = list(hunks or [])

    def payload(self) -> dict[str, typing.Any]:
        """返回可序列化的变更载荷。"""
        payload = {
            "path"                : self.path,
            "action"              : self.action,
            "old_content"         : self.old_content,
            "new_content"         : self.new_content,
            "source_path"         : self.source_path,
            "overwritten_content" : self.overwritten_content
        }
        payload["hunks"] = self.hunks
        return payload


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
        hunks: list[dict[str, typing.Any]] | None = None,
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
                overwritten_content=overwritten_content,
                hunks=hunks,
            )
        )

    def add_planned_change(self, item: dict[str, typing.Any]) -> None:
        """从已成功写入的计划项追加变更。"""
        action = str(item.get("action") or "")
        self.add_change(
            path=str(item.get("path") or ""),
            action=action,
            old_content=item.get("old_content"),
            new_content=item.get("new_content"),
            source_path=item.get("source_path"),
            overwritten_content=item.get("overwritten_content"),
            hunks=_content_hunks(
                item.get("old_content"),
                item.get("new_content"),
                action=action,
            ),
            exact=bool(item.get("delta_exact", True))
        )

    def payload(self) -> dict[str, typing.Any]:
        """返回可放入工具结果的 delta 载荷。"""
        return {
            "exact"   : self.exact,
            "changes" : [change.payload() for change in self.changes]
        }


def _content_hunks(
    old_content: typing.Any,
    new_content: typing.Any,
    *,
    action: str,
) -> list[dict[str, typing.Any]]:
    """从原生补丁结果生成一次性的结构化 diff hunk。"""
    old_lines = str(old_content or "").splitlines()
    new_lines = str(new_content or "").splitlines()

    if action == "create":
        return [{
            "lines": [
                {
                    "kind": "add",
                    "text": text,
                    "old_line": None,
                    "new_line": index,
                }
                for index, text in enumerate(new_lines, start=1)
            ]
        }]
    if action == "delete":
        return [{
            "lines": [
                {
                    "kind": "remove",
                    "text": text,
                    "old_line": index,
                    "new_line": None,
                }
                for index, text in enumerate(old_lines, start=1)
            ]
        }]

    matcher = difflib.SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    )
    hunks: list[dict[str, typing.Any]] = []
    for group in matcher.get_grouped_opcodes(n=3):
        lines: list[dict[str, typing.Any]] = []
        for tag, old_start, old_end, new_start, new_end in group:
            if tag == "equal":
                lines.extend(
                    {
                        "kind": "context",
                        "text": old_lines[old_index],
                        "old_line": old_index + 1,
                        "new_line": new_index + 1,
                    }
                    for old_index, new_index in zip(
                        range(old_start, old_end),
                        range(new_start, new_end),
                        strict=True,
                    )
                )
            if tag in {"delete", "replace"}:
                lines.extend(
                    {
                        "kind": "remove",
                        "text": old_lines[index],
                        "old_line": index + 1,
                        "new_line": None,
                    }
                    for index in range(old_start, old_end)
                )
            if tag in {"insert", "replace"}:
                lines.extend(
                    {
                        "kind": "add",
                        "text": new_lines[index],
                        "old_line": None,
                        "new_line": index + 1,
                    }
                    for index in range(new_start, new_end)
                )
        if lines:
            hunks.append({"lines": lines})
    return hunks


if __name__ == '__main__':
    pass
