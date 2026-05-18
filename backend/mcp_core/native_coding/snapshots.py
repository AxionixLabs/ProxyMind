# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
from backend.mcp_core.native_coding.base import NativeCodingComponent
from backend.utilities import const


class SnapshotTools(NativeCodingComponent):

    def rollback_run(
        self,
        *,
        session_id: str,
        run_id: str | None = None
    ) -> dict[str, typing.Any]:
        session = self.sessions.get(str(session_id or ""))
        if not isinstance(session, dict):
            return self._fail("session_not_found", session_id=session_id)
        runs = [item for item in (session.get("runs") or []) if isinstance(item, dict)]
        run = None
        if run_id:
            run = next((item for item in runs if item.get("run_id") == run_id), None)
        elif runs:
            run = runs[-1]
        if not isinstance(run, dict):
            return self._fail("run_not_found", session_id=session_id, run_id=run_id)
        snapshot = run.get("snapshot") if isinstance(run.get("snapshot"), dict) else {}
        files = snapshot.get("files") if isinstance(snapshot, dict) else []
        if not files:
            return self._fail("snapshot_not_found", session_id=session_id, run_id=run.get("run_id"))

        restored: list[dict[str, typing.Any]] = []
        for item in files:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            target = self._resolve(str(item["path"]))
            existed = bool(item.get("existed"))
            if existed:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(item.get("content") or ""), encoding=const.CHARSET, newline="")
                restored.append({
                    "path": self._rel(target),
                    "action": "restored",
                    "sha256": item.get("sha256")
                })
            else:
                if target.exists() and target.is_file():
                    target.unlink()
                    action = "removed_created_file"
                else:
                    action = "already_absent"
                restored.append({
                    "path": self._rel(target),
                    "action": action,
                    "sha256": None
                })
        run["rolled_back"] = True
        run["rollback_at"] = time.time()
        run["rollback"] = restored
        session.setdefault("rollbacks", []).append({
            "run_id": run.get("run_id"),
            "run_index": run.get("run_index"),
            "restored": restored,
            "rolled_back_at": run["rollback_at"]
        })
        return self._ok(
            f"rollback ok session_id={session_id} run_id={run.get('run_id')} files={len(restored)}",
            session_id=session_id,
            run_id=run.get("run_id"),
            run_index=run.get("run_index"),
            restored=restored,
            restored_count=len(restored)
        )

    def _create_run_snapshot(self, preflight: dict[str, typing.Any]) -> dict[str, typing.Any]:
        files: list[dict[str, typing.Any]] = []
        seen: set[str] = set()
        for check in preflight.get("checks") or []:
            if not isinstance(check, dict) or not check.get("ok"):
                continue
            tool = str(check.get("tool") or "")
            paths: list[str] = []
            if tool in {"workspace_write_file", "workspace_apply_patch"} and check.get("path"):
                paths.append(str(check["path"]))
            elif tool == "workspace_apply_unified_patch":
                for item in check.get("files") or []:
                    if isinstance(item, dict) and item.get("path"):
                        paths.append(str(item["path"]))
            for path in paths:
                if path in seen:
                    continue
                seen.add(path)
                target = self._resolve(path)
                if target.is_file():
                    content = target.read_text(encoding=const.CHARSET, errors=const.IGNORE)
                    files.append({
                        "path": self._rel(target),
                        "existed": True,
                        "sha256": self._sha256(content.encode(const.CHARSET, const.IGNORE)),
                        "content": content
                    })
                else:
                    files.append({
                        "path": self._rel(target),
                        "existed": False,
                        "sha256": None,
                        "content": ""
                    })
        return {
            "created_at": time.time(),
            "files": files,
            "file_count": len(files)
        }

    @staticmethod
    def _public_snapshot(snapshot: dict[str, typing.Any]) -> dict[str, typing.Any]:
        return {
            "created_at": snapshot.get("created_at"),
            "file_count": snapshot.get("file_count"),
            "files": [
                {
                    "path": item.get("path"),
                    "existed": item.get("existed"),
                    "sha256": item.get("sha256")
                }
                for item in snapshot.get("files") or []
                if isinstance(item, dict)
            ]
        }


if __name__ == '__main__':
    pass
