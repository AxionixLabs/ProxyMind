# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)


class ChangeSummaryTools(NativeCodingComponent):
    """汇总 Git 变更、验证记录和工作区风险信息。"""

    def __init__(self, core: NativeCodingBase, *, git_tools: typing.Any) -> None:
        """保存共享运行时上下文和 Git 工具依赖。"""
        super().__init__(core)

        self._git_tools = git_tools

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
            "reason"                 : payload.get("reason"),
            "shell_write_detected"   : bool(payload.get("shell_write_detected")),
            "shell_file_changes"     : payload.get("shell_file_changes") if isinstance(payload.get("shell_file_changes"), dict) else None
        }

    @classmethod
    def _validation_history_summary(
        cls,
        payloads: list[dict[str, typing.Any]] | None
    ) -> dict[str, typing.Any]:
        """整理本轮会话记录过的 shell_exec 验证历史。"""
        if not isinstance(payloads, list) or not payloads:
            return {
                "command_count"                : 0,
                "commands"                     : [],
                "passed_count"                 : 0,
                "failed_count"                 : 0,
                "timed_out_count"              : 0,
                "cloud_sandbox_required_count" : 0,
                "latest"                       : cls._validation_summary(None),
                "all_passed"                   : False,
                "any_failed"                   : False
            }

        entries = [
            cls._validation_summary(item)
            for item in payloads
            if isinstance(item, dict) and item
        ]
        commands = [
            item.get("command")
            for item in entries
            if isinstance(item.get("command"), list)
        ]

        return {
            "command_count"                : len(entries),
            "commands"                     : commands,
            "passed_count"                 : sum(1 for item in entries if item.get("status") == "passed"),
            "failed_count"                 : sum(1 for item in entries if item.get("status") == "failed"),
            "timed_out_count"              : sum(1 for item in entries if item.get("status") == "timed_out"),
            "cloud_sandbox_required_count" : sum(1 for item in entries if item.get("status") == "cloud_sandbox_required"),
            "latest"                       : entries[-1] if entries else cls._validation_summary(None),
            "all_passed"                   : bool(entries) and all(item.get("status") == "passed" for item in entries),
            "any_failed"                   : any(item.get("status") in {"failed", "timed_out"} for item in entries)
        }

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
            "cloud_sandbox_supported" : runtime.get("cloud_sandbox_supported")
        }

    @staticmethod
    def _tracked_diff_stats(
        diff_stats: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """从结构化 diff 统计中提取已跟踪文件的汇总字段。"""
        if not isinstance(diff_stats, dict):
            return {}
        if diff_stats.get("source") != "git_diff_structured":
            return diff_stats

        sections = [
            item for item in (diff_stats.get("unstaged"), diff_stats.get("staged"))
            if isinstance(item, dict)
        ]
        per_file = [
            item
            for section in sections
            for item in (section.get("per_file") or [])
            if isinstance(item, dict)
        ]
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
            "source": "git_numstat",
            "truncated": any(bool(section.get("truncated")) for section in sections)
        }

    @staticmethod
    def _shell_write_risk(
        validation: dict[str, typing.Any]
    ) -> dict[str, typing.Any] | None:
        """从验证摘要中提取 shell 写文件风险。"""
        if not bool(validation.get("shell_write_detected")):
            return None

        changes = validation.get("shell_file_changes") if isinstance(validation.get("shell_file_changes"), dict) else {}

        return {
            "kind"           : "shell_write_detected",
            "message"        : "latest shell_exec wrote or attempted to write workspace files",
            "command"        : validation.get("command"),
            "reason"         : validation.get("reason"),
            "created"        : list(changes.get("created") or []),
            "modified"       : list(changes.get("modified") or []),
            "deleted"        : list(changes.get("deleted") or []),
            "change_count"   : changes.get("change_count")
        }

    @staticmethod
    def _verification_assessment(
        *,
        blockers: list[dict[str, typing.Any]],
        warnings: list[dict[str, typing.Any]],
        changed_files: list[dict[str, typing.Any]],
        validation: dict[str, typing.Any],
        validation_history: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """根据可见工作区状态判断当前摘要是否存在阻断项。"""
        has_changes   = bool(changed_files)
        validation_ok = validation.get("validation_ok")
        sufficient    = not blockers
        history       = validation_history if isinstance(validation_history, dict) else {}

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
            "sufficient"         : sufficient,
            "validation_ok"      : validation_ok,
            "validation"         : validation,
            "validation_history" : history,
            "validation_status"  : validation.get("status"),
            "commands"           : history.get("commands") or validation.get("commands") or [],
            "last_exit_code"     : validation.get("exit_code"),
            "stdout_preview"     : validation.get("stdout_preview"),
            "stderr_preview"     : validation.get("stderr_preview"),
            "not_run_reason"     : validation.get("not_run_reason"),
            "has_changes"        : has_changes,
            "blocker_count"      : len(blockers),
            "warning_count"      : len(warnings),
            "reason"             : reason
        }

    @staticmethod
    def _preview(value: typing.Any, *, limit: int) -> str:
        """按字符上限生成文本预览。"""
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"

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
                target = self.resolve_path(path)
            except ValueError:
                previews.append({"path": path, "ok": False, "reason": "path_outside_workspace"})
                continue
            if not target.is_file():
                previews.append({"path": path, "ok": False, "reason": "not_a_file"})
                continue
            if not self.looks_text(target):
                previews.append({"path": path, "ok": False, "reason": "file_not_text"})
                continue

            size  = target.stat().st_size
            limit = 1200

            content = self.decode_bytes(target.read_bytes()[:limit])

            previews.append({
                "path"           : path,
                "ok"             : True,
                "size"           : size,
                "byte_truncated" : size > limit,
                "preview"        : content
            })

        return previews

    async def change_summary(
        self,
        *,
        max_diff_chars: int = 12000,
        include_untracked_preview: bool = True
    ) -> dict[str, typing.Any]:
        """生成变更摘要、风险项和验证充分性判断。"""
        status = await self._git_tools.git_status()
        diff   = await self._git_tools.git_diff(max_chars=max_diff_chars)

        status_data = status.get("data") or {}
        diff_data   = diff.get("data") or {}

        status_text           = str(status_data.get("stdout") or "")
        diff_text             = str(diff_data.get("stdout") or "")
        changed_files         = self._parse_git_status_short(status_text)
        structured_diff_stats = diff_data.get("diff_stats") if isinstance(diff_data.get("diff_stats"), dict) else {}
        diff_stats            = self._tracked_diff_stats(structured_diff_stats)
        untracked_previews    = self._summarize_untracked_files(changed_files) if include_untracked_preview else []
        validation            = self._validation_summary(self.last_shell_result)
        validation_history    = self._validation_history_summary(self.validation_history)

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

        shell_write_risk = self._shell_write_risk(validation)
        if shell_write_risk:
            warnings.append(shell_write_risk)

        ready = not blockers

        verification = self._verification_assessment(
            blockers=blockers,
            warnings=warnings,
            changed_files=changed_files,
            validation=validation,
            validation_history=validation_history
        )

        return self.ok_result(
            f"change summary ready={ready} files={len(changed_files)} blockers={len(blockers)} warnings={len(warnings)}",
            ready=ready,
            verification=verification,
            blockers=blockers,
            warnings=warnings,
            changed_files=changed_files,
            git_status=status_data,
            untracked_previews=untracked_previews,
            file_count=len(changed_files),
            diff_stats=diff_stats,
            structured_diff_stats=structured_diff_stats,
            validation=validation,
            validation_history=validation_history,
            shell_write_risk=shell_write_risk
        )


if __name__ == '__main__':
    pass
