# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.utilities import const


class TextPatchOperations(NativeCodingComponent):
    """提供面向文本文件的补丁操作入口。"""

    def __init__(self, core: NativeCodingBase, *, planner: typing.Any, diagnostics: typing.Any) -> None:
        """保存共享运行时上下文和补丁执行依赖。"""
        super().__init__(core)

        self._planner     = planner
        self._diagnostics = diagnostics

    def apply_patch(
        self,
        *,
        path: str,
        old_text: str,
        new_text: str,
        expected_replacements: int = 1,
        expected_sha256: str | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """对单个文本文件执行精确片段替换。"""
        target = self.resolve_path(path)

        if not target.is_file():
            return self.fail_result("file_not_found", path=path)
        if not str(old_text or ""):
            return self.fail_result("old_text_empty", path=self.relative_path(target))
        if conflict := self.conflict_guard(target, expected_sha256=expected_sha256, force=force):
            return conflict

        current  = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)
        count    = current.count(old_text)
        expected = max(1, int(expected_replacements or 1))

        if count != expected:
            diagnostics = self._diagnostics.replacement_mismatch_diagnostics(
                current=current,
                old_text=old_text
            )
            data = {
                "path": self.relative_path(target),
                "found": count,
                "expected": expected,
                "old_text_preview": self._diagnostics.diagnostic_preview(old_text, limit=600),
                "current_preview": self._diagnostics.diagnostic_preview(current, limit=1200),
                **diagnostics,
                "suggested_next_action": "refresh_file_snapshot_or_use_write_file"
            }
            self._diagnostics.log_patch_failure(
                "workspace_apply_patch", "replacement_count_mismatch", data
            )
            return self.fail_result(
                "replacement_count_mismatch", **data
            )

        updated = current.replace(old_text, new_text, expected)
        size    = len(updated.encode(const.CHARSET, const.IGNORE))

        if size > self.max_write_bytes:
            return self.fail_result(
                "content_too_large", size=size, max_bytes=self.max_write_bytes
            )

        target.write_text(updated, encoding=const.CHARSET, newline="")

        return self.ok_result(
            f"workspace patch ok path={self.relative_path(target)} replacements={expected}",
            path=self.relative_path(target),
            replacements=expected,
            sha256=self.sha256_bytes(updated.encode(const.CHARSET, const.IGNORE))
        )

    def apply_unified_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """解析并应用标准 unified diff 补丁。"""
        planned_result = self._planner.plan_unified_patch(
            patch=patch,
            expected_sha256=expected_sha256,
            force=force
        )
        if not planned_result.get("ok"):
            data = dict(planned_result.get("data") or {})
            data.pop("reason", None)
            data = self._diagnostics.with_unified_patch_diagnostics(
                reason=str(planned_result["reason"]),
                data=data,
                patch=patch
            )
            self._diagnostics.log_patch_failure(
                "workspace_apply_unified_patch", str(planned_result["reason"]), data
            )
            return self.fail_result(planned_result["reason"], **data)

        planned = planned_result["planned"]
        for item in planned:
            if item["action"] == "delete":
                item["target"].unlink()
                continue
            item["target"].parent.mkdir(parents=True, exist_ok=True)
            item["target"].write_text(item["content"], encoding=const.CHARSET, newline="")
            self._diagnostics.refresh_written_file_mtime(item["target"])
            if item["action"] == "rename" and item.get("source_target"):
                item["source_target"].unlink()

        changed_files = [
            self._planner.public_unified_patch_file(item) for item in planned
        ]
        created_files = [
            item for item in changed_files if item.get("action") == "create"
        ]
        updated_files = [
            item for item in changed_files if item.get("action") == "modify"
        ]
        deleted_files = [
            item for item in changed_files if item.get("action") == "delete"
        ]

        return self.ok_result(
            f"workspace unified patch ok files={len(planned)} hunks={sum(item['hunks'] for item in planned)}",
            files=[
                {
                    "path"            : item["path"],
                    "source_path"     : item["source_path"],
                    "action"          : item["action"],
                    "hunks"           : item["hunks"],
                    "relocated_hunks" : item["relocated_hunks"],
                    "corrected_hunks" : item["corrected_hunks"],
                    "sha256"          : item["sha256"],
                    "sha256_before"   : item["sha256_before"],
                    "sha256_after"    : item["sha256_after"],
                    "added_lines"     : item["added_lines"],
                    "removed_lines"   : item["removed_lines"],
                    "replacements"    : item["replacements"]
                }
                for item in planned
            ],
            changed_files=changed_files,
            created_files=created_files,
            updated_files=updated_files,
            deleted_files=deleted_files,
            file_count=len(planned),
            hunk_count=sum(item["hunks"] for item in planned),
            added_lines=sum(item["added_lines"] for item in planned),
            removed_lines=sum(item["removed_lines"] for item in planned),
            replacements=sum(item["replacements"] for item in planned),
            relocated_hunk_count=sum(len(item["relocated_hunks"]) for item in planned),
            corrected_hunk_count=sum(len(item["corrected_hunks"]) for item in planned)
        )


if __name__ == '__main__':
    pass
