# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.utilities import const


class UnifiedPatchPlanner(NativeCodingComponent):
    """预检查 unified diff 并生成文件写入计划。"""

    def __init__(
        self,
        core: NativeCodingBase,
        *,
        parser: typing.Any,
        applier: typing.Any,
        diagnostics: typing.Any
    ) -> None:
        """保存共享运行时上下文和 unified patch 处理依赖。"""
        super().__init__(core)

        self._parser      = parser
        self._applier     = applier
        self._diagnostics = diagnostics

    @staticmethod
    def _unified_patch_line_stats(hunks: list[dict[str, typing.Any]]) -> dict[str, int]:
        """统计 unified patch hunk 中的新增、删除和上下文行数。"""
        added   = 0
        removed = 0
        context = 0

        for hunk in hunks:
            for raw_line in hunk.get("lines") or []:
                marker = str(raw_line.get("marker") or "") if isinstance(raw_line, dict) else str(raw_line)[:1]
                if marker == "+":
                    added += 1
                elif marker == "-":
                    removed += 1
                elif marker == " ":
                    context += 1

        return {
            "added_lines"   : added,
            "removed_lines" : removed,
            "context_lines" : context,
            "replacements"  : min(added, removed)
        }

    @staticmethod
    def _unified_patch_count_corrections(hunks: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
        """收集 hunk 头声明行数与实际行数不一致的修正信息。"""
        corrections: list[dict[str, typing.Any]] = []
        for index, hunk in enumerate(hunks, start=1):
            if not bool(hunk.get("count_corrected")):
                continue
            corrections.append({
                "hunk"               : index,
                "header"             : hunk.get("header"),
                "declared_old_count" : hunk.get("declared_old_count"),
                "declared_new_count" : hunk.get("declared_new_count"),
                "actual_old_count"   : hunk.get("old_count"),
                "actual_new_count"   : hunk.get("new_count")
            })
        return corrections

    @staticmethod
    def public_unified_patch_file(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """把内部变更计划转换为对外返回的文件摘要。"""
        return {
            "path"            : item.get("path"),
            "source_path"     : item.get("source_path"),
            "action"          : item.get("action"),
            "hunks"           : item.get("hunks"),
            "relocated_hunks" : list(item.get("relocated_hunks") or []),
            "corrected_hunks" : list(item.get("corrected_hunks") or []),
            "sha256_before"   : item.get("sha256_before"),
            "sha256_after"    : item.get("sha256_after"),
            "added_lines"     : item.get("added_lines"),
            "removed_lines"   : item.get("removed_lines"),
            "replacements"    : item.get("replacements")
        }

    def plan_unified_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False,
        virtual_files: dict[str, str | None] | None = None
    ) -> dict[str, typing.Any]:
        """预检查 unified diff，并生成待写入文件的变更计划。"""
        parsed = self._parser.parse_unified_patch(patch)
        if not parsed.get("ok"):
            return {
                "ok"     : False,
                "reason" : parsed["reason"],
                "data"   : parsed.get("data") or {}
            }

        expected_map = {
            str(k).strip(): str(v).strip().lower()
            for k, v in (expected_sha256 or {}).items()
            if str(k).strip() and str(v).strip()
        }
        planned: list[dict[str, typing.Any]] = []

        seen_paths: set[str] = set()

        for item in parsed["files"]:

            path   = str(item["path"])
            action = str(item.get("action") or "modify")

            try:
                target = self.resolve_path(path)
            except ValueError as exc:
                return {
                    "ok": False,
                    "reason": "path_outside_workspace",
                    "data": {
                        "path": path,
                        "error": str(exc)
                    }
                }

            rel           = self.relative_path(target)
            source_target = target
            source_rel    = rel

            if action == "rename":
                source_path = str(item.get("old_path") or "")
                try:
                    source_target = self.resolve_path(source_path)
                except ValueError as exc:
                    return {
                        "ok": False,
                        "reason": "path_outside_workspace",
                        "data": {
                        "path": source_path,
                        "error": str(exc)
                    }
                }
                source_rel = self.relative_path(source_target)

            duplicate_paths = [rel]
            if action == "rename":
                duplicate_paths.append(source_rel)
            duplicate = next((item_path for item_path in duplicate_paths if item_path in seen_paths), "")
            if duplicate:
                return {
                    "ok": False,
                    "reason": "unified_patch_duplicate_file",
                    "data": {
                        "path": duplicate
                    }
                }
            seen_paths.update(duplicate_paths)

            has_virtual     = rel in (virtual_files or {})
            virtual_content = (virtual_files or {}).get(rel)
            virtual_exists  = has_virtual and virtual_content is not None
            disk_exists     = target.is_file()
            exists          = virtual_exists if has_virtual else disk_exists

            if action == "create" and exists:
                return {
                    "ok": False,
                    "reason": "file_already_exists",
                    "data": {
                        "path": path
                    }
                }
            if action == "rename":
                if not source_target.is_file():
                    return {
                        "ok": False,
                        "reason": "file_not_found",
                        "data": {
                            "path": source_rel
                        }
                    }
                if target.exists():
                    return {
                        "ok": False,
                        "reason": "file_already_exists",
                        "data": {
                            "path": rel
                        }
                    }
            if action in {"modify", "delete"} and not exists:
                return {
                    "ok": False,
                    "reason": "file_not_found",
                    "data": {
                        "path": path
                    }
                }
            if action in {"modify", "delete", "rename"}:
                expected = expected_map.get(path) or expected_map.get(rel)
                if action == "rename":
                    expected = expected or expected_map.get(source_rel) or expected_map.get(str(item.get("old_path") or ""))
                    if conflict := self.conflict_guard(source_target, expected_sha256=expected, force=force):
                        return {
                            "ok"     : False,
                            "reason" : (conflict.get("data") or {}).get("reason") or "file_changed_since_read",
                            "data"   : conflict.get("data") or {}
                        }
                    current = self._diagnostics.read_text_preserve_newlines(source_target)
                elif has_virtual:
                    current = str(virtual_content or "")
                    current_sha256 = self.sha256_bytes(current.encode(const.CHARSET, const.IGNORE))
                    if expected and not force and expected != current_sha256:
                        return {
                            "ok": False,
                            "reason": "file_changed_since_read",
                            "data": {
                                "path": rel,
                                "expected_sha256": expected,
                                "current_sha256": current_sha256
                            }
                        }
                else:
                    if conflict := self.conflict_guard(target, expected_sha256=expected, force=force):
                        return {
                            "ok"     : False,
                            "reason" : (conflict.get("data") or {}).get("reason") or "file_changed_since_read",
                            "data"   : conflict.get("data") or {}
                        }
                    current = self._diagnostics.read_text_preserve_newlines(target)
            else:
                current = ""

            sha256_before = (
                self.sha256_bytes(current.encode(const.CHARSET, const.IGNORE))
                if action in {"modify", "delete", "rename"} else None
            )

            applied = self._applier.apply_unified_hunks(current, item["hunks"])
            if not applied.get("ok"):
                reason = str(applied["reason"])
                data   = {"path": path, **(applied.get("data") or {})}
                data   = self._diagnostics.with_unified_patch_diagnostics(data=data, patch=patch)

                return {
                    "ok"     : False,
                    "reason" : reason,
                    "data"   : data
                }

            content = str(applied["content"])
            if action == "delete" and content:
                return {
                    "ok": False,
                    "reason": "unified_patch_delete_leaves_content",
                    "data": {
                        "path": path
                    }
                }
            size = len(content.encode(const.CHARSET, const.IGNORE))
            if size > self.max_write_bytes:
                return {
                    "ok": False,
                    "reason": "content_too_large",
                    "data": {
                        "path": path,
                        "size": size,
                        "max_bytes": self.max_write_bytes
                    }
                }

            line_stats      = self._unified_patch_line_stats(item["hunks"])
            corrected_hunks = self._unified_patch_count_corrections(item["hunks"])
            sha256_content  = self.sha256_bytes(content.encode(const.CHARSET, const.IGNORE))

            planned.append({
                "path"            : path,
                "source_path"     : source_rel if action == "rename" else None,
                "action"          : action,
                "target"          : target,
                "source_target"   : source_target if action == "rename" else None,
                "content"         : content,
                "hunks"           : len(item["hunks"]),
                "relocated_hunks" : list(applied.get("relocated_hunks") or []),
                "corrected_hunks" : corrected_hunks,
                "sha256"          : sha256_content,
                "sha256_before"   : sha256_before,
                "sha256_after"    : None if action == "delete" else sha256_content,
                **line_stats
            })

        return {"ok": True, "planned": planned}


if __name__ == '__main__':
    pass
