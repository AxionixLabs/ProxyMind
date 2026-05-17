# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent


class ChangeSummaryTools(NativeCodingComponent):

    async def change_summary(
        self,
        *,
        session_id: str | None = None,
        max_diff_chars: int = 12000
    ) -> dict[str, typing.Any]:
        status = await self.git_status()
        diff   = await self.git_diff(max_chars=max_diff_chars)

        status_data     = status.get("data") or {}
        diff_data       = diff.get("data") or {}
        status_text     = str(status_data.get("stdout") or "")
        diff_text       = str(diff_data.get("stdout") or "")
        changed_files   = self._parse_git_status_short(status_text)
        diff_stats      = self._summarize_diff_text(diff_text)
        session         = self.sessions.get(str(session_id or "")) if session_id else self._latest_session()
        session_summary = session.get("summary") if isinstance(session, dict) else None
        latest_run      = (session.get("runs") or [])[-1] if isinstance(session, dict) and session.get("runs") else None

        blockers: list[dict[str, typing.Any]] = []
        warnings: list[dict[str, typing.Any]] = []

        if not bool(status_data.get("available", True)):
            warnings.append({"kind": "git_unavailable", "message": "workspace is not a git repository"})
        if any(item.get("conflict") for item in changed_files):
            blockers.append({"kind": "merge_conflict", "message": "git status reports unresolved conflicts"})
        untracked = [item for item in changed_files if item.get("untracked")]
        if untracked:
            warnings.append({
                "kind"  : "untracked_files",
                "count" : len(untracked),
                "paths" : [item["path"] for item in untracked[:20]]
            })
        if bool(diff_data.get("truncated")) or "...[truncated " in diff_text:
            warnings.append({"kind": "diff_truncated", "message": "diff output was truncated"})
        if isinstance(latest_run, dict):
            preflight = latest_run.get("preflight") or {}
            verify = latest_run.get("verify")
            if preflight and not bool(preflight.get("ok")):
                blockers.append({"kind": "preflight_failed", "run_id": latest_run.get("run_id")})
            if verify and not bool(verify.get("ok")):
                blockers.append({"kind": "verification_failed", "run_id": latest_run.get("run_id")})
            if verify is None:
                warnings.append({"kind": "verification_missing", "run_id": latest_run.get("run_id")})
        else:
            warnings.append({"kind": "session_missing", "message": "no native coding session was found"})

        ready = not blockers
        return self._ok(
            f"change summary ready={ready} files={len(changed_files)} blockers={len(blockers)} warnings={len(warnings)}",
            ready=ready,
            blockers=blockers,
            warnings=warnings,
            changed_files=changed_files,
            file_count=len(changed_files),
            diff_stats=diff_stats,
            status=status_text,
            diff=diff_text,
            session_id=session.get("session_id") if isinstance(session, dict) else None,
            latest_run={
                "run_id"       : latest_run.get("run_id"),
                "run_index"    : latest_run.get("run_index"),
                "ok"           : latest_run.get("ok"),
                "verify_ok"    : bool((latest_run.get("verify") or {}).get("ok")) if latest_run.get("verify") else None,
                "preflight_ok" : bool((latest_run.get("preflight") or {}).get("ok")) if latest_run.get("preflight") else None
            } if isinstance(latest_run, dict) else None,
            session_summary=session_summary
        )

    def _latest_session(self) -> dict[str, typing.Any] | None:
        if not self.sessions:
            return None
        return list(self.sessions.values())[-1]

    @staticmethod
    def _parse_git_status_short(status: str) -> list[dict[str, typing.Any]]:
        files: list[dict[str, typing.Any]] = []
        conflict_pairs = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
        for raw in str(status or "").splitlines():
            if not raw:
                continue
            if len(raw) < 4:
                continue
            xy = raw[:2]
            path_text = raw[3:].strip()
            original = None
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
    def _summarize_diff_text(diff: str) -> dict[str, typing.Any]:
        added = 0
        deleted = 0
        files: list[str] = []

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
            "changed_lines" : added + deleted
        }


if __name__ == '__main__':
    pass
