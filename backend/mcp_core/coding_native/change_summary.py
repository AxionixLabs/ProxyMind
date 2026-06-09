# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_core.coding_native.base import NativeCodingComponent


class ChangeSummaryTools(NativeCodingComponent):

    async def change_summary(
        self,
        *,
        max_diff_chars: int = 12000,
        include_untracked_preview: bool = True
    ) -> dict[str, typing.Any]:
        """生成变更摘要、风险项和验证充分性判断。"""
        status = await self.git_status()
        diff   = await self.git_diff(max_chars=max_diff_chars)

        status_data = status.get("data") or {}
        diff_data   = diff.get("data") or {}

        numstat = await self._git(
            ["diff", "--numstat"], output_limit=max_diff_chars
        ) if bool(status_data.get("available", True)) else {}

        numstat_data       = (numstat.get("data") or {}) if isinstance(numstat, dict) else {}
        status_text        = str(status_data.get("stdout") or "")
        diff_text          = str(diff_data.get("stdout") or "")
        numstat_text       = str(numstat_data.get("stdout") or "")
        changed_files      = self._parse_git_status_short(status_text)
        diff_stats         = self._summarize_diff_text(diff_text, numstat_text=numstat_text)
        untracked_previews = self._summarize_untracked_files(changed_files) if include_untracked_preview else []

        blockers: list[dict[str, typing.Any]] = []
        warnings: list[dict[str, typing.Any]] = []

        if not bool(status_data.get("available", True)):
            warnings.append({
                "kind"    : "git_unavailable",
                "message" : "workspace is not a git repository"
            })

        if any(item.get("conflict") for item in changed_files):
            blockers.append({
                "kind"    : "merge_conflict",
                "message" : "git status reports unresolved conflicts"
            })

        untracked = [item for item in changed_files if item.get("untracked")]
        if untracked:
            warnings.append({
                "kind"  : "untracked_files",
                "count" : len(untracked),
                "paths" : [item["path"] for item in untracked[:20]]
            })

        if bool(diff_data.get("truncated")) or "...[truncated " in diff_text:
            warnings.append({
                "kind"    : "diff_truncated",
                "message" : "diff output was truncated"
            })

        ready = not blockers

        verification = self._verification_assessment(
            blockers=blockers,
            warnings=warnings,
            changed_files=changed_files
        )

        return self._ok(
            f"change summary ready={ready} files={len(changed_files)} blockers={len(blockers)} warnings={len(warnings)}",
            ready=ready,
            verification=verification,
            blockers=blockers,
            warnings=warnings,
            changed_files=changed_files,
            untracked_previews=untracked_previews,
            file_count=len(changed_files),
            diff_stats=diff_stats,
            git_status=status_text,
            stdout=status_text,
            diff=diff_text
        )

    def _summarize_untracked_files(
        self,
        changed_files: list[dict[str, typing.Any]]
    ) -> list[dict[str, typing.Any]]:
        """为未跟踪文本文件生成有限内容预览。"""
        previews: list[dict[str, typing.Any]] = []

        for item in changed_files:
            if not item.get("untracked"):
                continue

            path = str(item.get("path") or "")
            try:
                target = self._resolve(path)
            except ValueError:
                previews.append({"path": path, "ok": False, "reason": "path_outside_workspace"})
                continue
            if not target.is_file():
                previews.append({"path": path, "ok": False, "reason": "not_a_file"})
                continue
            if not self._looks_text(target):
                previews.append({"path": path, "ok": False, "reason": "file_not_text"})
                continue

            size  = target.stat().st_size
            limit = 1200

            content = self._decode(target.read_bytes()[:limit])

            previews.append({
                "path"           : path,
                "ok"             : True,
                "size"           : size,
                "byte_truncated" : size > limit,
                "preview"        : content
            })

        return previews

    @staticmethod
    def _parse_git_status_short(
        status: str
    ) -> list[dict[str, typing.Any]]:
        """解析 git status --short 输出为文件状态列表。"""
        files: list[dict[str, typing.Any]] = []

        conflict_pairs = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}

        for raw in str(status or "").splitlines():
            if not raw:
                continue
            if len(raw) < 4:
                continue

            xy        = raw[:2]
            path_text = raw[3:].strip()
            original  = None

            if " -> " in path_text:
                original, path_text = path_text.split(" -> ", 1)

            item = {
                "path"            : path_text,
                "status"          : xy,
                "index_status"    : xy[0],
                "worktree_status" : xy[1],
                "staged"          : xy[0] not in {" ", "?"},
                "unstaged"        : xy[1] not in {" ", "?"},
                "untracked"       : xy == "??",
                "conflict"        : xy in conflict_pairs or "U" in xy
            }
            if original:
                item["original_path"] = original
            files.append(item)

        return files

    @staticmethod
    def _summarize_diff_text(
        diff: str,
        *,
        numstat_text: str = ""
    ) -> dict[str, typing.Any]:
        """根据 git diff 文本或 numstat 输出统计变更规模。"""
        added   = 0
        deleted = 0

        files: list[str] = []
        per_file: list[dict[str, typing.Any]] = []

        for line in str(numstat_text or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added_text, deleted_text, path = parts[0], parts[1], parts[2]

            file_added   = None if added_text == "-" else int(added_text) if added_text.isdigit() else None
            file_deleted = None if deleted_text == "-" else int(deleted_text) if deleted_text.isdigit() else None

            per_file.append({
                "path"          : path,
                "added_lines"   : file_added,
                "deleted_lines" : file_deleted,
                "binary"        : file_added is None or file_deleted is None
            })

        if per_file:
            numeric_files = [item for item in per_file if not item.get("binary")]
            return {
                "files": [str(item.get("path") or "") for item in per_file],
                "file_count": len(per_file),
                "added_lines": sum(int(item.get("added_lines") or 0) for item in numeric_files),
                "deleted_lines": sum(int(item.get("deleted_lines") or 0) for item in numeric_files),
                "changed_lines": sum(
                    int(item.get("added_lines") or 0) + int(item.get("deleted_lines") or 0)
                    for item in numeric_files
                ),
                "binary_files": [str(item.get("path") or "") for item in per_file if item.get("binary")],
                "per_file": per_file,
                "source": "git_numstat"
            }

        for line in str(diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                    files.append(path)
                continue
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                deleted += 1

        return {
            "files"         : files,
            "file_count"    : len(files),
            "added_lines"   : added,
            "deleted_lines" : deleted,
            "changed_lines" : added + deleted,
            "binary_files"  : [],
            "per_file"      : [],
            "source"        : "diff_text"
        }

    @staticmethod
    def _runtime_failure_summary(
        payload: dict[str, typing.Any]
    ) -> dict[str, typing.Any] | None:
        """提取运行时失败的关键诊断字段。"""
        runtime = payload.get("runtime") if isinstance(payload, dict) else None
        if not isinstance(runtime, dict):
            return None
        if bool(runtime.get("ok")):
            return None

        return {
            "name"                    : runtime.get("name"),
            "reason"                  : runtime.get("reason"),
            "suggested_next_action"   : runtime.get("suggested_next_action"),
            "cloud_sandbox_supported" : runtime.get("cloud_sandbox_supported")
        }

    @staticmethod
    def _verification_assessment(
        *,
        blockers: list[dict[str, typing.Any]],
        warnings: list[dict[str, typing.Any]],
        changed_files: list[dict[str, typing.Any]]
    ) -> dict[str, typing.Any]:
        """根据可见工作区状态判断当前摘要是否存在阻断项。"""
        has_changes = bool(changed_files)
        sufficient  = not blockers

        reason = "workspace_state_checked"
        if not has_changes:
            reason = "no_changes_detected"
        elif blockers:
            reason = "blockers_present"
        elif warnings:
            reason = "workspace_state_checked_with_warnings"

        return {
            "sufficient"    : sufficient,
            "validation_ok" : None,
            "has_changes"   : has_changes,
            "blocker_count" : len(blockers),
            "warning_count" : len(warnings),
            "reason"        : reason
        }


if __name__ == '__main__':
    pass
