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
        validation         = self._validation_summary(self.last_shell_result)

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

        if validation.get("status") == "failed":
            blockers.append({
                "kind"      : "validation_failed",
                "message"   : "latest validation command failed",
                "command"   : validation.get("command"),
                "exit_code" : validation.get("exit_code")
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

        if validation.get("status") == "not_run" and changed_files:
            warnings.append({
                "kind"    : "validation_not_run",
                "message" : "no validation command has been recorded for current native coding session",
                "reason"  : validation.get("not_run_reason")
            })

        if validation.get("status") == "cloud_sandbox_required":
            warnings.append({
                "kind"    : "validation_pending_cloud_sandbox",
                "message" : "latest validation command requires cloud sandbox execution",
                "command" : validation.get("command")
            })

        ready = not blockers

        verification = self._verification_assessment(
            blockers=blockers,
            warnings=warnings,
            changed_files=changed_files,
            validation=validation
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
            validation=validation,
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
                "files"         : [str(item.get("path") or "") for item in per_file],
                "file_count"    : len(per_file),
                "added_lines"   : sum(int(item.get("added_lines") or 0) for item in numeric_files),
                "deleted_lines" : sum(int(item.get("deleted_lines") or 0) for item in numeric_files),
                "changed_lines" : sum(
                    int(item.get("added_lines") or 0) + int(item.get("deleted_lines") or 0)
                    for item in numeric_files
                ),
                "binary_files" : [str(item.get("path") or "") for item in per_file if item.get("binary")],
                "per_file"     : per_file,
                "source"       : "git_numstat"
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

    @classmethod
    def _validation_summary(
        cls,
        payload: dict[str, typing.Any] | None
    ) -> dict[str, typing.Any]:
        """把最近一次 shell_exec 结果整理为最终摘要可消费的验证证据。"""
        if not isinstance(payload, dict) or not payload:
            return {
                "status"         : "not_run",
                "validation_ok"  : None,
                "sufficient"     : False,
                "not_run_reason" : "no_shell_exec_recorded",
                "command"        : None,
                "commands"       : []
            }

        command        = payload.get("command") if isinstance(payload.get("command"), list) else None
        requires_cloud = bool(payload.get("requires_cloud_sandbox"))
        timed_out      = bool(payload.get("timed_out"))
        ok             = bool(payload.get("ok"))
        exit_code      = payload.get("exit_code")

        if requires_cloud:
            status = "cloud_sandbox_required"
            validation_ok = None
        elif timed_out:
            status = "timed_out"
            validation_ok = False
        elif ok:
            status = "passed"
            validation_ok = True
        else:
            status = "failed"
            validation_ok = False

        runtime_failure = cls._runtime_failure_summary(payload)

        return {
            "status"                 : status,
            "validation_ok"          : validation_ok,
            "sufficient"             : validation_ok is True,
            "command"                : command,
            "commands"               : [command] if command else [],
            "cwd"                    : payload.get("cwd"),
            "exit_code"              : exit_code,
            "timed_out"              : timed_out,
            "elapsed_ms"             : payload.get("elapsed_ms"),
            "execution_target"       : payload.get("execution_target"),
            "requires_cloud_sandbox" : requires_cloud,
            "stdout_preview"         : cls._preview(payload.get("stdout"), limit=1200),
            "stderr_preview"         : cls._preview(payload.get("stderr"), limit=1200),
            "stdout_truncated"       : bool(payload.get("stdout_truncated")),
            "stderr_truncated"       : bool(payload.get("stderr_truncated")),
            "truncated"              : bool(payload.get("truncated")),
            "runtime_failure"        : runtime_failure,
            "shell_write_detected"   : bool(payload.get("shell_write_detected")),
            "shell_file_changes"     : payload.get("shell_file_changes") if isinstance(payload.get("shell_file_changes"), dict) else None
        }

    @staticmethod
    def _verification_assessment(
        *,
        blockers: list[dict[str, typing.Any]],
        warnings: list[dict[str, typing.Any]],
        changed_files: list[dict[str, typing.Any]],
        validation: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """根据可见工作区状态判断当前摘要是否存在阻断项。"""
        has_changes   = bool(changed_files)
        validation_ok = validation.get("validation_ok")
        sufficient    = not blockers and (validation_ok is True or not has_changes)

        reason = "workspace_state_checked"

        if not has_changes:
            reason = "no_changes_detected"
        elif blockers:
            reason = "blockers_present"
        elif validation_ok:
            reason = "workspace_state_checked_and_validated"
        elif validation.get("status") == "cloud_sandbox_required":
            reason = "validation_pending_cloud_sandbox"
        elif warnings:
            reason = "workspace_state_checked_with_warnings"

        return {
            "sufficient"        : sufficient,
            "validation_ok"     : validation_ok,
            "validation"        : validation,
            "validation_status" : validation.get("status"),
            "commands"          : validation.get("commands") or [],
            "last_exit_code"    : validation.get("exit_code"),
            "stdout_preview"    : validation.get("stdout_preview"),
            "stderr_preview"    : validation.get("stderr_preview"),
            "not_run_reason"    : validation.get("not_run_reason"),
            "has_changes"       : has_changes,
            "blocker_count"     : len(blockers),
            "warning_count"     : len(warnings),
            "reason"            : reason
        }

    @staticmethod
    def _preview(value: typing.Any, *, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"


if __name__ == '__main__':
    pass
