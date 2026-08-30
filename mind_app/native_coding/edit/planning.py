# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.native_coding.base import (
    NativeCodingBase,
    NativeCodingComponent
)
from metadata import const
from mind_app.native_coding.edit.types import PatchHunk


class PatchPlanner(NativeCodingComponent):
    """预检查文本补丁并生成文件写入计划。"""

    def __init__(
        self,
        core: NativeCodingBase,
        *,
        parser: typing.Any,
        applier: typing.Any,
        diagnostics: typing.Any
    ) -> None:
        """保存共享运行时上下文和 patch 处理依赖。"""
        super().__init__(core)

        self._parser      = parser
        self._applier     = applier
        self._diagnostics = diagnostics

    @staticmethod
    def _patch_line_stats(hunks: list[PatchHunk]) -> dict[str, int]:
        """统计 patch hunk 中的新增、删除和上下文行数。"""
        added: int   = 0
        removed: int = 0
        context: int = 0

        for hunk in hunks:
            for raw_line in hunk.entries:
                marker = raw_line.marker
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
    def _native_create_content(hunks: list[PatchHunk]) -> str:
        """从 Add File hunk 中生成新文件内容。"""
        lines: list[str] = []
        for hunk in hunks:
            for raw_line in hunk.entries:
                marker = raw_line.marker
                if marker != "+":
                    continue
                text = raw_line.text
                if raw_line.no_newline:
                    lines.append(text)
                else:
                    lines.append(f"{text}\n")

        return "".join(lines)

    @staticmethod
    def public_patch_file(item: dict[str, typing.Any]) -> dict[str, typing.Any]:
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

    @staticmethod
    def _delta_exact_for_source(target: typing.Any) -> bool:
        """判断源文件内容是否能用于精确 delta。"""
        try:
            if target.is_symlink():
                return False
            target.read_bytes().decode(const.CHARSET)
            return True
        except (OSError, UnicodeDecodeError):
            return False

    def plan_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """预检查文本补丁，并生成待写入文件的变更计划。"""
        parsed = self._parser.parse_patch(patch)
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
        seen_paths: set[str]                 = set()

        for item in parsed["files"]:
            path   = item.path
            action = item.action

            try:
                target = self.resolve_path(path)
            except ValueError as exc:
                return {
                    "ok"     : False,
                    "reason" : "path_outside_workspace",
                    "data"   : {"path": path, "error": str(exc)}
                }

            rel           = self.relative_path(target)
            source_target = target
            source_rel    = rel

            if action == "rename":
                source_path = item.old_path
                try:
                    source_target = self.resolve_path(source_path)
                except ValueError as exc:
                    return {
                        "ok"     : False,
                        "reason" : "path_outside_workspace",
                        "data"   : {"path": source_path, "error": str(exc)}
                    }
                source_rel = self.relative_path(source_target)

            duplicate_paths = [rel]
            if action == "rename":
                duplicate_paths.append(source_rel)
            duplicate = next((item_path for item_path in duplicate_paths if item_path in seen_paths), "")
            if duplicate:
                return {
                    "ok"     : False,
                    "reason" : "native_patch_duplicate_file",
                    "data"   : {"path": duplicate}
                }
            seen_paths.update(duplicate_paths)

            exists = target.is_file()
            if action == "create" and target.exists():
                return {"ok": False, "reason": "file_already_exists", "data": {"path": rel}}

            if action == "rename":
                if not source_target.is_file():
                    return {"ok": False, "reason": "file_not_found", "data": {"path": source_rel}}
                if target.exists():
                    return {"ok": False, "reason": "file_already_exists", "data": {"path": rel}}

            if action in {"modify", "delete"} and not exists:
                return {"ok": False, "reason": "file_not_found", "data": {"path": rel}}

            if action in {"modify", "delete", "rename"}:
                expected = expected_map.get(path) or expected_map.get(rel)
                if action == "rename":
                    expected = expected or expected_map.get(source_rel) or expected_map.get(item.old_path)
                    if conflict := self.conflict_guard(source_target, expected_sha256=expected, force=force):
                        return {
                            "ok"     : False,
                            "reason" : (conflict.get("data") or {}).get("reason") or "file_changed_since_read",
                            "data"   : conflict.get("data") or {}
                        }

                    current     = self._diagnostics.read_text_preserve_newlines(source_target)
                    delta_exact = self._delta_exact_for_source(source_target)

                else:
                    if conflict := self.conflict_guard(target, expected_sha256=expected, force=force):
                        return {
                            "ok"     : False,
                            "reason" : (conflict.get("data") or {}).get("reason") or "file_changed_since_read",
                            "data"   : conflict.get("data") or {}
                        }

                    current     = self._diagnostics.read_text_preserve_newlines(target)
                    delta_exact = self._delta_exact_for_source(target)

            else:
                current     = ""
                delta_exact = True

            sha256_before = (
                self.sha256_bytes(current.encode(const.CHARSET, const.IGNORE))
                if action in {"modify", "delete", "rename"} else None
            )

            if action == "create":
                content = self._native_create_content(item.hunks)
                applied: dict[str, typing.Any] = {"relocated_hunks": []}
            elif action == "delete" and not item.hunks:
                content = ""
                applied = {"relocated_hunks": []}
            else:
                applied = self._applier.apply_patch_hunks(current, item.hunks)
                if not applied.get("ok"):
                    reason = str(applied["reason"])
                    data   = {"path": path, **(applied.get("data") or {})}
                    data   = self._diagnostics.with_patch_diagnostics(data=data, patch=patch)

                    return {"ok": False, "reason": reason, "data": data}

                content = str(applied["content"])

            if action == "delete" and content:
                return {"ok": False, "reason": "native_patch_delete_leaves_content", "data": {"path": path}}

            size = len(content.encode(const.CHARSET, const.IGNORE))
            if size > self.max_write_bytes:
                return {
                    "ok"     : False,
                    "reason" : "content_too_large",
                    "data"   : {"path": path, "size": size, "max_bytes": self.max_write_bytes}
                }

            line_stats     = self._patch_line_stats(item.hunks)
            sha256_content = self.sha256_bytes(content.encode(const.CHARSET, const.IGNORE))

            planned.append({
                "path"                : rel,
                "source_path"         : source_rel if action == "rename" else None,
                "action"              : action,
                "target"              : target,
                "source_target"       : source_target if action == "rename" else None,
                "content"             : content,
                "old_content"         : None if action == "create" else current,
                "new_content"         : None if action == "delete" else content,
                "overwritten_content" : None,
                "delta_exact"         : delta_exact,
                "hunks"               : len(item.hunks),
                "relocated_hunks"     : list(applied.get("relocated_hunks") or []),
                "corrected_hunks"     : [],
                "sha256"              : sha256_content,
                "sha256_before"       : sha256_before,
                "sha256_after"        : None if action == "delete" else sha256_content,
                **line_stats
            })

        return {"ok": True, "planned": planned}


if __name__ == '__main__':
    pass
