# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from infrastructure.workspace.patches.diff import DiffRenderer


class WorkspaceDiffTracker:
    """在内存中合并多次文本变更并生成净差异。"""

    def __init__(self) -> None:
        """初始化差异跟踪状态。"""
        self.invalidated: bool = False
        self.baseline_by_path: dict[str, str | None] = {}
        self.current_by_path: dict[str, str] = {}
        self.origin_by_current_path: dict[str, str] = {}
        self.unified_diff: str = ""

    @staticmethod
    def _optional_text(value: typing.Any) -> str | None:
        """把可选值转换为可选文本。"""
        if value is None:
            return None
        return str(value)

    def _render_entries(self) -> list[dict[str, str | None]]:
        """生成待渲染的净差异条目。"""
        entries: list[dict[str, str | None]] = []
        origins_to_current = {
            origin: current
            for current, origin in self.origin_by_current_path.items()
        }

        for origin in sorted(self.baseline_by_path):
            old_content = self.baseline_by_path.get(origin)
            current_path = origins_to_current.get(origin)
            new_content = (
                self.current_by_path.get(current_path)
                if current_path is not None else None
            )

            if old_content is None and new_content is None:
                continue
            if current_path == origin and old_content == new_content:
                continue

            entries.append({
                "old_path": origin,
                "new_path": current_path or origin,
                "old_content": old_content,
                "new_content": new_content
            })

        return entries

    def _track_change(self, change: dict[str, typing.Any]) -> None:
        """根据变更类型更新内存状态。"""
        action = str(change.get("action") or "").strip()
        path = str(change.get("path") or "").strip()
        source_path = str(change.get("source_path") or "").strip()
        old_content = self._optional_text(change.get("old_content"))
        new_content = self._optional_text(change.get("new_content"))

        if action == "create":
            self._apply_create(path, new_content)
            return
        if action == "modify":
            self._apply_modify(path, old_content, new_content)
            return
        if action == "delete":
            self._apply_delete(path, old_content)
            return
        if action == "rename":
            self._apply_rename(
                source_path=source_path,
                path=path,
                old_content=old_content,
                new_content=new_content
            )
            return
        self.invalidate()

    def _apply_create(self, path: str, new_content: str | None) -> None:
        """记录新增文本内容。"""
        if not path or new_content is None:
            self.invalidate()
            return
        self.baseline_by_path.setdefault(path, None)
        self.current_by_path[path] = new_content
        self.origin_by_current_path[path] = self.origin_by_current_path.get(path, path)

    def _apply_modify(
        self,
        path: str,
        old_content: str | None,
        new_content: str | None
    ) -> None:
        """记录文本内容修改。"""
        if not path or old_content is None or new_content is None:
            self.invalidate()
            return
        origin = self.origin_by_current_path.get(path, path)
        self.baseline_by_path.setdefault(origin, old_content)
        self.current_by_path[path] = new_content
        self.origin_by_current_path[path] = origin

    def _apply_delete(self, path: str, old_content: str | None) -> None:
        """记录文本内容删除。"""
        if not path or old_content is None:
            self.invalidate()
            return
        origin = self.origin_by_current_path.get(path, path)
        self.baseline_by_path.setdefault(origin, old_content)
        self.current_by_path.pop(path, None)
        self.origin_by_current_path.pop(path, None)

    def _apply_rename(
        self,
        *,
        source_path: str,
        path: str,
        old_content: str | None,
        new_content: str | None
    ) -> None:
        """记录路径变化及其文本内容。"""
        if not source_path or not path or old_content is None or new_content is None:
            self.invalidate()
            return
        origin = self.origin_by_current_path.get(source_path, source_path)
        self.baseline_by_path.setdefault(origin, old_content)
        self.current_by_path.pop(source_path, None)
        self.origin_by_current_path.pop(source_path, None)
        self.current_by_path[path] = new_content
        self.origin_by_current_path[path] = origin

    def invalidate(self) -> None:
        """标记跟踪状态失效并清空当前差异。"""
        self.invalidated = True
        self.baseline_by_path.clear()
        self.current_by_path.clear()
        self.origin_by_current_path.clear()
        self.unified_diff = ""

    def track_delta(self, delta: dict[str, typing.Any]) -> str:
        """消费一次文本变更并返回更新后的差异文本。"""
        if self.invalidated:
            return ""
        if not isinstance(delta, dict) or not bool(delta.get("exact", False)):
            self.invalidate()
            return ""

        changes = delta.get("changes")
        if not isinstance(changes, list):
            self.invalidate()
            return ""

        for change in changes:
            if not isinstance(change, dict):
                self.invalidate()
                return ""
            self._track_change(change)

        self.unified_diff = DiffRenderer.render_many(self._render_entries())
        return self.unified_diff


if __name__ == '__main__':
    pass
