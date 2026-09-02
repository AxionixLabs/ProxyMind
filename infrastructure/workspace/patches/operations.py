# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.patches.delta import AppliedPatchDelta
from infrastructure.workspace.context import (
    WorkspaceComponent,
    WorkspaceContext,
)
from infrastructure.workspace.patches.diagnostics import PatchDiagnostics
from infrastructure.workspace.patches.planner import PatchPlanner
from metadata import const


class TextPatchOperations(WorkspaceComponent):
    """提供文本补丁操作入口。"""

    def __init__(
        self,
        core: WorkspaceContext,
        *,
        planner: PatchPlanner,
        diagnostics: PatchDiagnostics,
    ) -> None:
        """保存共享运行时上下文和补丁执行依赖。"""
        super().__init__(core)
        self._planner = planner
        self._diagnostics = diagnostics

    def apply_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """解析并应用受支持的文本补丁。"""
        planned_result = self._planner.plan_patch(
            patch=patch,
            expected_sha256=expected_sha256,
            force=force
        )
        if not planned_result.get("ok"):
            data = dict(planned_result.get("data") or {})
            data.pop("reason", None)
            data = self._diagnostics.with_patch_diagnostics(
                data=data,
                patch=patch
            )
            return self.fail_result(planned_result["reason"], **data)

        planned = planned_result["planned"]
        delta = AppliedPatchDelta()

        for item in planned:
            try:
                if item["action"] == "delete":
                    item["target"].unlink()
                    delta.add_planned_change(item)
                    continue

                item["target"].parent.mkdir(parents=True, exist_ok=True)
                item["target"].write_text(item["content"], encoding=const.CHARSET, newline="")

                self._diagnostics.refresh_written_file_mtime(item["target"])

                if item["action"] == "rename" and item.get("source_target"):
                    item["source_target"].unlink()
                delta.add_planned_change(item)
            except OSError as exc:
                delta.mark_inexact()
                return self.fail_result(
                    "patch_write_failed",
                    path=item.get("path"),
                    action=item.get("action"),
                    error=str(exc),
                    delta=delta.payload()
                )

        changed_files = [
            self._planner.public_patch_file(item) for item in planned
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
            f"apply patch ok files={len(planned)} hunks={sum(item['hunks'] for item in planned)}",
            files=[
                {
                    "path": item["path"],
                    "source_path": item["source_path"],
                    "action": item["action"],
                    "hunks": item["hunks"],
                    "relocated_hunks": item["relocated_hunks"],
                    "corrected_hunks": item["corrected_hunks"],
                    "sha256": item["sha256"],
                    "sha256_before": item["sha256_before"],
                    "sha256_after": item["sha256_after"],
                    "added_lines": item["added_lines"],
                    "removed_lines": item["removed_lines"],
                    "replacements": item["replacements"]
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
            corrected_hunk_count=sum(len(item["corrected_hunks"]) for item in planned),
            delta=delta.payload()
        )

    def preview_patch(
        self,
        *,
        patch: str,
        expected_sha256: dict[str, str] | None = None,
        force: bool = False
    ) -> dict[str, typing.Any]:
        """在不写入工作区的前提下生成补丁展示所需的精确变化。"""
        planned_result = self._planner.plan_patch(
            patch=patch,
            expected_sha256=expected_sha256,
            force=force,
        )
        if not planned_result.get("ok"):
            data = dict(planned_result.get("data") or {})
            data.pop("reason", None)
            data = self._diagnostics.with_patch_diagnostics(
                data=data,
                patch=patch,
            )
            return self.fail_result(planned_result["reason"], **data)

        planned = planned_result["planned"]
        delta = AppliedPatchDelta()

        for item in planned:
            delta.add_planned_change(item)

        changed_files = [
            self._planner.public_patch_file(item) for item in planned
        ]
        return self.ok_result(
            f"preview patch ok files={len(planned)} hunks={sum(item['hunks'] for item in planned)}",
            files=changed_files,
            file_count=len(planned),
            hunk_count=sum(item["hunks"] for item in planned),
            added_lines=sum(item["added_lines"] for item in planned),
            removed_lines=sum(item["removed_lines"] for item in planned),
            replacements=sum(item["replacements"] for item in planned),
            relocated_hunk_count=sum(len(item["relocated_hunks"]) for item in planned),
            corrected_hunk_count=sum(len(item["corrected_hunks"]) for item in planned),
            delta=delta.payload(),
        )


if __name__ == '__main__':
    pass
